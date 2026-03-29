"""
Donation receipt PDF generation for Website Donation.

Security model:
  Receipts are stored as *public* files so WhatsApp (an external caller) can
  fetch them via a plain URL.  Guessability is prevented by embedding a
  secrets.token_hex(8) token (64 bits of entropy) in every filename, e.g.
      WDON-2026-00001_3a9f1c7e4b82d051.pdf
  Even if an attacker knows the docname and date, they cannot enumerate or
  brute-force the token.

Naming convention mirrors frappe_s3_attachment's key_generator approach
(random prefix + meaningful name) adapted for the local file system.

wkhtmltopdf note:
  Frappe's get_pdf() calls scrub_urls() which expands relative asset paths to
  the external site URL (e.g. https://techniti.wecanwewillfdn.org/assets/...).
  Background (RQ) workers often can't resolve that hostname — HostNotFoundError.
  _localize_html() pre-converts those paths to http://127.0.0.1:PORT/... so
  scrub_urls() sees them as already-absolute and leaves them alone, letting
  wkhtmltopdf fetch assets from the local Gunicorn process instead.
"""
import re
import secrets
from urllib.parse import urlparse

import frappe
from frappe.utils import get_url


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_donation_receipt_pdf(docname, print_format=None):
    """
    Generate a PDF receipt for *docname* (Website Donation) and save it as a
    public Frappe File attachment with a secure random token in the filename.

    Overwrites any previously generated receipt for the same document
    (old file is deleted before the new one is written).

    Returns the absolute public URL of the new PDF, or None on failure.
    """
    from frappe.utils.pdf import get_pdf
    from frappe.utils.file_manager import save_file

    try:
        _delete_old_receipt(docname)

        html = frappe.get_print(
            "Website Donation",
            docname,
            print_format=print_format,
            no_letterhead=0,
        )
        html = _localize_html(html)
        pdf_bytes = get_pdf(html)

        # Secure filename: {docname}_{16-hex-char token}.pdf
        token = secrets.token_hex(8)          # 64 bits of entropy
        safe_name = docname.replace("/", "-")
        filename = f"{safe_name}_{token}.pdf"

        _file = save_file(
            fname=filename,
            content=pdf_bytes,
            dt="Website Donation",
            dn=docname,
            is_private=0,           # must be public for WhatsApp to access
        )

        full_url = get_url(_file.file_url)     # absolute URL for WhatsApp

        frappe.db.set_value(
            "Website Donation", docname,
            "custom_pdf_url", full_url,
            update_modified=False,
        )
        frappe.db.commit()

        return full_url

    except Exception as e:
        frappe.log_error(
            title=f"Donation PDF Error - {docname}",
            message=str(e),
        )
        return None


@frappe.whitelist()
def regenerate_donation_pdf(docname):
    """
    Whitelisted — called from the form button or via bench execute.

    Example:
        bench execute techniti.donation_pdf.regenerate_donation_pdf \
            --kwargs '{"docname": "WDON-2026-00001"}'
    """
    frappe.enqueue(
        "techniti.donation_pdf._generate_donation_pdf_bg",
        queue="short",
        timeout=120,
        docname=docname,
    )
    frappe.msgprint("Receipt PDF generation has been queued.", alert=True)


# ---------------------------------------------------------------------------
# RQ background worker
# ---------------------------------------------------------------------------

def _generate_donation_pdf_bg(docname, print_format=None):
    """Entry point called by the RQ worker process."""
    generate_donation_receipt_pdf(docname, print_format=print_format)


# ---------------------------------------------------------------------------
# doc_event hook
# ---------------------------------------------------------------------------

def on_donation_submit(doc, method):
    """
    Enqueue PDF generation when a Website Donation is submitted.
    Runs *before* the wildcard WhatsApp notification is enqueued so the
    worker queue preserves FIFO order: PDF first, then WhatsApp.
    """
    frappe.enqueue(
        "techniti.donation_pdf._generate_donation_pdf_bg",
        queue="short",
        timeout=120,
        docname=doc.name,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _localize_html(html):
    """
    Replace relative and site-absolute asset URLs with http://127.0.0.1:PORT/...
    so wkhtmltopdf fetches CSS/JS/images from the local Gunicorn process instead
    of trying to reach the external site hostname (which fails in background jobs).

    Frappe's scrub_urls() (called inside get_pdf) only converts paths that do NOT
    start with 'http', so pre-converting to localhost leaves them untouched.
    """
    site_url = get_url().rstrip("/")
    conf = frappe.get_site_config()
    port = conf.get("http_port") or conf.get("webserver_port") or 8000
    local_base = f"http://127.0.0.1:{port}"

    # 1. Replace any already-absolute site URLs (e.g. from letterheads / get_url() calls)
    html = html.replace(site_url, local_base)

    # 2. Replace root-relative paths in src/href/action attributes: src="/..."
    html = re.sub(
        r'((?:src|href|action)=")(\/(?!\/))',
        rf"\1{local_base}/",
        html,
    )

    # 3. Replace root-relative paths in CSS url() notation: url('/...') or url("/...")
    html = re.sub(
        r'(url\([\'"]?)(\/(?!\/))',
        rf"\1{local_base}/",
        html,
    )

    return html


def _delete_old_receipt(docname):
    """Delete the previously generated receipt File document (if any)."""
    old_url = frappe.db.get_value("Website Donation", docname, "custom_pdf_url")
    if not old_url:
        return

    # Strip base URL to get the relative file_url stored on the File doc
    relative_url = urlparse(old_url).path   # e.g. /files/WDON-...pdf

    old_files = frappe.get_all(
        "File",
        filters={
            "attached_to_doctype": "Website Donation",
            "attached_to_name": docname,
            "file_url": relative_url,
        },
        fields=["name"],
        limit=1,
    )
    for f in old_files:
        try:
            frappe.delete_doc("File", f.name, ignore_permissions=True, force=True)
        except Exception:
            pass
