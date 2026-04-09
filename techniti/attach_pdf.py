"""
Generic PDF generation and attachment for any Frappe DocType.

─────────────────────────────────────────────────────────────────────────────
TO ADD A NEW DOCTYPE — only touch hooks.py:
─────────────────────────────────────────────────────────────────────────────

1.  Add the hook trigger:

        doc_events = {
            "Your DocType": {
                "on_submit": "techniti.attach_pdf.on_submit_attach_pdf",
            }
        }

2.  Add config (optional — all keys have sensible defaults):

        attach_pdf_config = {
            "Your DocType": {
                "pdf_url_field":  "custom_pdf_url",  # field to write URL into
                "print_format":   None,               # None = DocType default
                "enqueue":        True,               # False = generate inline (sync)
            }
        }

That's it.  No code changes required.

─────────────────────────────────────────────────────────────────────────────
Security model
─────────────────────────────────────────────────────────────────────────────
Receipts are stored as *public* files so external callers (WhatsApp, email
links, etc.) can fetch them without a Frappe session.  Guessability is
prevented by embedding secrets.token_hex(8) (64 bits of entropy) in every
filename, e.g.  WDON-2026-00001_3a9f1c7e4b82d051.pdf

─────────────────────────────────────────────────────────────────────────────
wkhtmltopdf / HostNotFoundError
─────────────────────────────────────────────────────────────────────────────
Frappe's get_pdf() calls scrub_urls() which expands relative asset paths to
the external site URL.  RQ workers often can't resolve that hostname.
_localize_html() pre-converts those paths to http://127.0.0.1:PORT/... so
wkhtmltopdf fetches assets from the local Gunicorn process instead.
"""
import re
import secrets
from urllib.parse import urlparse

import frappe
from frappe.utils import get_url


DEFAULT_PDF_URL_FIELD = "custom_pdf_url"


# ---------------------------------------------------------------------------
# doc_event hook — add to hooks.py for any DocType
# ---------------------------------------------------------------------------

def on_submit_attach_pdf(doc, method):
    """
    Generic on_submit hook.  Reads per-DocType config from attach_pdf_config
    in hooks.py and either enqueues or runs PDF generation inline.

    hooks.py minimal example (all config keys are optional):
        doc_events = {
            "Sales Invoice": {"on_submit": "techniti.attach_pdf.on_submit_attach_pdf"},
        }
        attach_pdf_config = {
            "Sales Invoice": {
                "pdf_url_field": "custom_pdf_url",
                "print_format":  "Tax Invoice",
                "enqueue":       True,
            }
        }
    """
    config = _get_config(doc.doctype)
    pdf_url_field  = config.get("pdf_url_field", DEFAULT_PDF_URL_FIELD)
    print_format   = config.get("print_format")
    no_letterhead  = config.get("no_letterhead", 0)
    should_enqueue = config.get("enqueue", True)   # default: async

    if should_enqueue:
        frappe.enqueue(
            "techniti.attach_pdf._generate_pdf_bg",
            queue="short",
            timeout=120,
            doctype=doc.doctype,
            docname=doc.name,
            pdf_url_field=pdf_url_field,
            print_format=print_format,
            no_letterhead=no_letterhead,
        )
    else:
        generate_and_attach_pdf(doc.doctype, doc.name, pdf_url_field=pdf_url_field, print_format=print_format, no_letterhead=no_letterhead)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_and_attach_pdf(doctype, docname, pdf_url_field=DEFAULT_PDF_URL_FIELD, print_format=None, no_letterhead=0):
    """
    Generate a PDF for *doctype/docname*, save it as a public File attachment
    with a secure random token in the filename, and write the absolute URL to
    *pdf_url_field* on the document.

    Deletes the previously generated PDF (if any) before writing the new one.
    Returns the absolute public URL, or None on failure.
    """
    from frappe.utils.pdf import get_pdf
    from frappe.utils.file_manager import save_file

    try:
        _delete_existing_pdf(doctype, docname, pdf_url_field)

        if print_format and no_letterhead:
            # ── Direct render path (Ticket) ──────────────────────────────────
            # Fetch the print format Jinja HTML straight from DB and render it
            # with the document context — no Frappe wrapper, no letterhead,
            # no asset URL injection.  Completely avoids the broken-image error.
            pf_html  = frappe.db.get_value("Print Format", print_format, "html") or ""
            doc_obj  = frappe.get_doc(doctype, docname)
            html     = frappe.render_template(pf_html, {"doc": doc_obj})
        else:
            # ── Original path (Website Donation, etc.) ────────────────────────
            # Unchanged from before — keeps existing PDF generation working.
            html = frappe.get_print(
                doctype, docname,
                print_format=print_format,
                no_letterhead=no_letterhead,
            )
            html = _localize_html(html)

        pdf_bytes = _get_pdf_safe(html)

        token = secrets.token_hex(8)
        safe_name = docname.replace("/", "-")
        filename = f"{safe_name}_{token}.pdf"

        _file = save_file(
            fname=filename,
            content=pdf_bytes,
            dt=doctype,
            dn=docname,
            is_private=0,   # public — required for WhatsApp / external access
        )

        full_url = get_url(_file.file_url)
        frappe.db.set_value(doctype, docname, pdf_url_field, full_url, update_modified=False)
        frappe.db.commit()

        return full_url

    except Exception as e:
        frappe.log_error(
            title=f"PDF Attach Error - {doctype} {docname}",
            message=str(e),
        )
        return None


@frappe.whitelist()
def regenerate_pdf(doctype, docname, pdf_url_field=None, print_format=None):
    """
    Whitelisted — called from a form button or via bench execute.
    Falls back to per-DocType config from hooks.py if pdf_url_field is omitted.

    Example:
        bench execute techniti.attach_pdf.regenerate_pdf \\
            --kwargs '{"doctype":"Website Donation","docname":"WDON-2026-00001"}'
    """
    config = _get_config(doctype)
    pdf_url_field  = pdf_url_field  or config.get("pdf_url_field", DEFAULT_PDF_URL_FIELD)
    print_format   = print_format   or config.get("print_format")

    frappe.enqueue(
        "techniti.attach_pdf._generate_pdf_bg",
        queue="short",
        timeout=120,
        doctype=doctype,
        docname=docname,
        pdf_url_field=pdf_url_field,
        print_format=print_format,
    )
    frappe.msgprint("PDF generation has been queued.", alert=True)


# ---------------------------------------------------------------------------
# RQ background worker
# ---------------------------------------------------------------------------

def _generate_pdf_bg(doctype, docname, pdf_url_field=DEFAULT_PDF_URL_FIELD, print_format=None, no_letterhead=0):
    """Entry point called by the RQ worker process."""
    generate_and_attach_pdf(doctype, docname, pdf_url_field=pdf_url_field, print_format=print_format, no_letterhead=no_letterhead)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_config(doctype):
    """
    Merge all attach_pdf_config dicts registered via hooks and return the
    config for *doctype* (empty dict if not configured).
    """
    merged = {}
    for hook_dict in (frappe.get_hooks("attach_pdf_config") or []):
        if isinstance(hook_dict, dict):
            merged.update(hook_dict)
    return merged.get(doctype, {})


def _delete_existing_pdf(doctype, docname, pdf_url_field):
    """Delete the previously generated PDF File document (if any)."""
    old_url = frappe.db.get_value(doctype, docname, pdf_url_field)
    if not old_url:
        return
    relative_url = urlparse(old_url).path   # /files/...
    for f in frappe.get_all(
        "File",
        filters={"attached_to_doctype": doctype, "attached_to_name": docname, "file_url": relative_url},
        fields=["name"],
        limit=1,
    ):
        try:
            frappe.delete_doc("File", f.name, ignore_permissions=True, force=True)
        except Exception:
            pass


def _get_pdf_safe(html):
    """
    Generate PDF bytes, bypassing Frappe's broken-image check when needed.

    Frappe's get_pdf() catches wkhtmltopdf's OSError and re-raises it as a
    ValidationError("PDF generation failed because of broken image links")
    ONLY when the resulting filedata is empty.  The --load-error-handling ignore
    flag tells wkhtmltopdf to continue despite missing images, but it still
    writes "ContentNotFoundError" to stderr — which Frappe intercepts.

    Strategy:
      1. Try Frappe's get_pdf() with load-error-handling:ignore (works most of
         the time once the option actually reaches wkhtmltopdf).
      2. If Frappe still throws "broken image links", strip ALL <img> tags and
         external url() references from the HTML and call pdfkit directly —
         completely bypassing Frappe's error check.
    """
    from frappe.utils.pdf import get_pdf

    _opts = {"load-error-handling": "ignore", "quiet": ""}

    try:
        return get_pdf(html, options=_opts)

    except Exception as e:
        if "broken image" not in str(e).lower():
            raise

        # ── Fallback: strip every external resource and call pdfkit directly ──
        clean = re.sub(r'<img[^>]*/?>', '', html, flags=re.IGNORECASE)
        # Remove external url() references in CSS (keep data: and relative paths)
        clean = re.sub(
            r'url\(["\']?https?://[^"\')\s]+["\']?\)',
            'none',
            clean,
            flags=re.IGNORECASE,
        )

        try:
            import pdfkit
            pdf_bytes = pdfkit.from_string(clean, False, options=_opts)
            if not pdf_bytes:
                frappe.throw("PDF generation returned empty output after image stripping.")
            return pdf_bytes
        except ImportError:
            # pdfkit not importable — re-raise original error
            raise e


def _localize_html(html):
    """
    Replace relative and site-absolute asset URLs with http://127.0.0.1:PORT/...
    so wkhtmltopdf fetches CSS/JS/images from the local Gunicorn process instead
    of trying to reach the external hostname (which fails in RQ background jobs).

    Frappe's scrub_urls() inside get_pdf() only converts paths that do NOT
    start with 'http', so pre-converting to localhost leaves them untouched.
    """
    site_url = get_url().rstrip("/")
    conf = frappe.get_site_config()
    port = conf.get("http_port") or conf.get("webserver_port") or 8000
    local_base = f"http://127.0.0.1:{port}"

    html = html.replace(site_url, local_base)
    html = re.sub(r'((?:src|href|action)=")(\/(?!\/))', rf"\1{local_base}/", html)
    html = re.sub(r'(url\([\'"]?)(\/(?!\/))', rf"\1{local_base}/", html)

    return html
