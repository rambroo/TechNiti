import json
import frappe
from frappe.model.document import Document

MAX_RETRY = 3
# Delay in minutes before each retry attempt: 1st retry after 5 min, 2nd after 10 min
RETRY_DELAYS = [5, 10]


class WhatsAppQueue(Document):

    def send(self):
        """
        Attempt to send this queued message via the Sparklebot API.
        Updates status to Sent on success, or reschedules/marks Error on failure.
        Called by process_whatsapp_queue() scheduler.
        """
        from techniti.whatsapp.whatsapp import SparklebotHandler

        self.db_set("status", "Sending", commit=True)
        try:
            handler = SparklebotHandler()

            if self.message_type == "template":
                try:
                    params = json.loads(self.field_params or "{}")
                except Exception:
                    params = {}
                success = handler.send_template(
                    self.phone,
                    self.template_name,
                    self.template_language or "en",
                    params,
                    self.reference_doctype,
                    self.reference_name,
                    header_document_url=self.header_document_url or None,
                )
            else:
                success = handler.send_text(
                    self.phone,
                    self.message,
                    self.reference_doctype,
                    self.reference_name,
                )

            if success:
                self.db_set("status", "Sent", commit=True)
                return True
            else:
                self._handle_failure("API returned failure — see Error Log for details")
                return False

        except Exception as e:
            self._handle_failure(str(e))
            return False

    def _handle_failure(self, error_msg):
        """Increment retry counter. Reschedule if under limit, else mark Error."""
        retry = (self.retry or 0) + 1
        if retry >= MAX_RETRY:
            self.db_set({
                "status": "Error",
                "retry": retry,
                "error": str(error_msg)[:2000],
            }, commit=True)
            frappe.log_error(
                title=f"WhatsApp Queue — Final Failure ({self.reference_doctype})",
                message=(
                    f"Exhausted {MAX_RETRY} attempts.\n"
                    f"Doc: {self.reference_name} | Phone: {self.phone}\n{error_msg}"
                )
            )
        else:
            delay_minutes = RETRY_DELAYS[retry - 1] if (retry - 1) < len(RETRY_DELAYS) else 10
            send_after = frappe.utils.add_to_date(
                frappe.utils.now_datetime(), minutes=delay_minutes
            )
            self.db_set({
                "status": "Not Sent",
                "retry": retry,
                "error": str(error_msg)[:2000],
                "send_after": send_after,
            }, commit=True)


def process_whatsapp_queue():
    """
    Scheduler entry point (runs every minute via hooks 'all' event).
    Fetches all pending WhatsApp Queue entries whose send_after has passed
    and attempts to send them.
    """
    if not _is_whatsapp_enabled():
        return

    rows = frappe.db.sql(
        """
        SELECT name FROM `tabWhatsApp Queue`
        WHERE status = 'Not Sent'
          AND (send_after IS NULL OR send_after <= %(now)s)
        ORDER BY priority DESC, retry ASC, creation ASC
        LIMIT 50
        """,
        {"now": frappe.utils.now_datetime()},
        as_dict=True,
    )

    for row in rows:
        try:
            doc = frappe.get_doc("WhatsApp Queue", row.name)
            # Re-check status after fetch to avoid race with parallel workers
            if doc.status == "Not Sent":
                doc.send()
        except Exception as e:
            frappe.log_error(
                title="WhatsApp Queue Processor Error",
                message=f"Queue entry: {row.name}\n{str(e)}"
            )


def _is_whatsapp_enabled():
    try:
        from techniti.whatsapp.whatsapp import safe_get_settings
        settings = safe_get_settings()
        return bool(settings and settings.enabled)
    except Exception:
        return False
