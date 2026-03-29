import requests
import frappe
import re
from frappe.utils import add_days, nowdate, date_diff, formatdate, now_datetime, add_to_date, get_datetime
from datetime import datetime, time, timedelta


# ============================================================================
# SETTINGS HELPER
# ============================================================================

def safe_get_settings():
    """Safely get WhatsApp Settings — returns None during install/migrate"""
    if frappe.flags.in_install or frappe.flags.in_patch or frappe.flags.in_migrate:
        return None

    if not frappe.db.exists("DocType", "WhatsApp Setting"):
        return None

    try:
        return frappe.get_single("WhatsApp Setting")
    except Exception as e:
        frappe.log_error(f"Could not load WhatsApp Settings: {e}", "WhatsApp Settings")
        return None


# ============================================================================
# SPARKLEBOT HANDLER
# ============================================================================

class SparklebotHandler:
    """
    WhatsApp message handler using the Sparklebot API.

    Supports:
      - Text messages  → POST {base_url}/messages/text
      - Template messages → POST {base_url}/messages/template
    """

    def __init__(self):
        self.settings = safe_get_settings()

        if not self.settings:
            frappe.throw("WhatsApp Settings not configured or not available")

        # Build base URL: api_base_url + tenant_slug + "/"
        base = (self.settings.api_base_url or "").rstrip("/")
        slug = (self.settings.tenant_slug or "").strip("/")
        self.base_url = f"{base}/{slug}" if base and slug else ""

        self.headers = {
            "Authorization": f"Bearer {self.settings.api_token}",
            "Content-Type": "application/json"
        }

    def is_enabled(self):
        """Check if WhatsApp is enabled and properly configured"""
        return bool(
            self.settings.enabled
            and self.settings.api_token
            and self.settings.tenant_slug
            and self.settings.api_base_url
        )

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def send_text(self, phone, message, doctype=None, docname=None):
        """Send a plain text message"""
        if not self.is_enabled():
            frappe.log_error("WhatsApp not configured", "WhatsApp Settings")
            return False

        clean_phone = self._build_phone(phone)
        if not clean_phone:
            frappe.log_error(
                f"Invalid phone number: {phone}",
                f"WhatsApp - {doctype} {docname}"
            )
            return False

        return self._send_text_message(clean_phone, message, doctype, docname)

    def send_template(self, phone, template_name, language, field_params,
                      doctype=None, docname=None, header_document_url=None):
        """
        Send a pre-approved WhatsApp template message.

        Args:
            phone: raw phone string
            template_name: Sparklebot template name
            language: language code (e.g. "en")
            field_params: dict  {"field_1": "Rohan", "field_2": "₹500", ...}
            header_document_url: optional PDF URL for document header component
        """
        if not self.is_enabled():
            frappe.log_error("WhatsApp not configured", "WhatsApp Settings")
            return False

        clean_phone = self._build_phone(phone)
        if not clean_phone:
            frappe.log_error(
                f"Invalid phone number: {phone}",
                f"WhatsApp - {doctype} {docname}"
            )
            return False

        return self._send_template_message(
            clean_phone, template_name, language, field_params, doctype, docname,
            header_document_url=header_document_url
        )

    # ------------------------------------------------------------------
    # Internal senders
    # ------------------------------------------------------------------

    def _send_text_message(self, phone, message, doctype, docname):
        """POST to /messages/text"""
        url = f"{self.base_url}/messages/text"
        payload = {
            "phone": phone,
            "message": message
        }

        try:
            response = requests.post(url, json=payload, headers=self.headers, timeout=30)
            return self._handle_response(response, phone, doctype, docname)
        except Exception as e:
            frappe.log_error(
                title="WhatsApp Text Send Error",
                message=f"DocType: {doctype} | Phone: {phone}\n{str(e)}"
            )
            return False

    def _send_template_message(self, phone, template_name, language,
                               field_params, doctype, docname,
                               header_document_url=None):
        """POST to /messages/template"""
        url = f"{self.base_url}/messages/template"
        payload = {
            "phone_number": phone,
            "template_name": template_name,
            "template_language": language or "en"
        }
        if header_document_url:
            payload["header_document_url"] = header_document_url
        payload.update(field_params)  # merges field_1, field_2, ...

        try:
            response = requests.post(url, json=payload, headers=self.headers, timeout=30)
            return self._handle_response(response, phone, doctype, docname)
        except Exception as e:
            frappe.log_error(
                title="WhatsApp Template Send Error",
                message=f"DocType: {doctype} | Template: {template_name} | Phone: {phone}\n{str(e)}"
            )
            return False

    def _handle_response(self, response, phone, doctype, docname):
        """Parse Sparklebot API response and return True on success"""
        if response.status_code not in (200, 201):
            frappe.log_error(
                title=f"WhatsApp HTTP Error - {doctype}",
                message=f"Status {response.status_code} | Phone: {phone}\n{response.text[:1000]}"
            )
            return False

        try:
            data = response.json()
            # Sparklebot success conditions:
            #   text messages    → {"message_id": "...", "status": "sent"}
            #   template messages → {"status": "success", "message": "template_sent_successfully", "data": {...}}
            if data.get("status") in ("success", "sent") or data.get("message_id"):
                # Extract wamid if available for traceability
                try:
                    wamid = (
                        data.get("message_id")
                        or data.get("data", {})
                               .get("whatsapp_response", {})
                               .get("messages", [{}])[0]
                               .get("id", "N/A")
                    )
                except Exception:
                    wamid = "N/A"
                frappe.logger("whatsapp").info(
                    f"WhatsApp sent | {doctype} | phone:{phone} | wamid:{wamid}"
                )
                return True
            else:
                frappe.log_error(
                    title=f"WhatsApp API Error - {doctype}",
                    message=f"Phone: {phone}\n{response.text[:1000]}"
                )
                return False
        except ValueError:
            frappe.log_error(
                title=f"WhatsApp API Error - {doctype}",
                message=f"Non-JSON response | Phone: {phone}\n{response.text[:500]}"
            )
            return False

    # ------------------------------------------------------------------
    # Phone number utilities
    # ------------------------------------------------------------------

    def _build_phone(self, phone):
        """
        Strip non-digits and prepend country code if not already present.
        Uses WhatsApp Setting.default_country_code (default "91").
        """
        if not phone:
            return None

        phone = re.sub(r'[^\d]', '', str(phone).strip())
        if not phone:
            return None

        country_code = str(self.settings.default_country_code or "91").strip()

        # If already prefixed with country code and total length is reasonable
        if phone.startswith(country_code) and len(phone) > len(country_code):
            return phone

        return country_code + phone

    def _clean_phone_number(self, phone):
        """
        Returns just the local number (digits only, no country code).
        Used internally by phone utility helpers.
        """
        if not phone:
            return None

        phone = re.sub(r'[^\d]', '', str(phone).strip())
        if not phone:
            return None

        country_code = str(self.settings.default_country_code or "91").strip()
        if phone.startswith(country_code) and len(phone) > len(country_code):
            phone = phone[len(country_code):]

        # Minimum sanity check — at least 7 digits
        if len(phone) >= 7:
            return phone

        return None


# ============================================================================
# TEMPLATE PARAMETER BUILDER
# ============================================================================

class TemplateParameterBuilder:
    """
    Resolves field_1 … field_10 values from a WhatsApp Message Template
    and the triggering document.
    """

    @staticmethod
    def build(template_doc, doc):
        """
        Returns a dict like {"field_1": "Rohan", "field_2": "₹500", ...}
        by iterating template_doc.parameters child table rows.
        """
        params = {}

        if not hasattr(template_doc, 'parameters') or not template_doc.parameters:
            return params

        for row in template_doc.parameters:
            field_key = f"field_{row.field_number}"

            if row.value_type == "Static Text":
                params[field_key] = row.static_value or ""
            else:
                # Document Field
                raw_value = TemplateParameterBuilder._get_doc_value(doc, row.document_field)
                params[field_key] = raw_value

        return params

    @staticmethod
    def _get_doc_value(doc, field_path):
        """
        Resolves a field path like "customer_name" or "due_date" from a document.
        Formats dates/currency values for WhatsApp display.
        """
        if not field_path:
            return ""

        field_path = field_path.strip()
        value = getattr(doc, field_path, None)

        if value is None:
            return ""

        # Format date
        if hasattr(value, 'strftime'):
            try:
                return formatdate(value)
            except Exception:
                return str(value)

        # Format currency fields (grand_total, total, amount, etc.)
        currency_fields = ['grand_total', 'total', 'amount', 'net_total',
                           'outstanding_amount', 'paid_amount', 'base_grand_total']
        if field_path in currency_fields:
            try:
                return f"₹{value:,.2f}" if value else ""
            except Exception:
                pass

        return str(value) if value else ""


# ============================================================================
# MESSAGE TEMPLATE HANDLER (text messages)
# ============================================================================

class MessageTemplateHandler:
    """Handles text-mode message template processing and placeholder replacement"""

    @staticmethod
    def clean_html(text):
        """Remove HTML tags and entities from text"""
        if not text:
            return text

        try:
            text = re.sub(r'<div class="ql-editor[^"]*"[^>]*>', '', text)
            text = re.sub(r'</div>', '', text)
            text = re.sub(r'<[^>]+>', '', text)

            html_entities = {
                '&nbsp;': ' ', '&amp;': '&', '&lt;': '<', '&gt;': '>',
                '&quot;': '"', '&#39;': "'", '&apos;': "'",
                '&hellip;': '...', '&mdash;': '—', '&ndash;': '–'
            }
            for entity, replacement in html_entities.items():
                text = text.replace(entity, replacement)

            text = re.sub(r'\s+', ' ', text).strip()
            text = re.sub(r'\n\s*\n', '\n', text)
            return text
        except Exception as e:
            frappe.log_error(f"Error cleaning HTML: {str(e)}", "WhatsApp HTML Cleanup")
            return text

    @staticmethod
    def format_whatsapp_message(message):
        """Normalise line breaks for WhatsApp"""
        if not message:
            return message

        replacements = [
            ('%0A\\n', '\n'), ('%0A', '\n'), ('\\n', '\n'), ('/n', '\n'),
            ('&lt;br&gt;', '\n'), ('<br>', '\n'), ('<br/>', '\n'),
            ('<BR>', '\n'), ('&nbsp;', ' '),
        ]
        for old, new in replacements:
            message = message.replace(old, new)

        message = re.sub(r'\n{3,}', '\n\n', message)
        lines = [line.rstrip() for line in message.split('\n')]
        return '\n'.join(lines).strip()

    @staticmethod
    def build_message(doc, notification, is_reminder=False,
                      target_date=None, target_time=None):
        """Build WhatsApp text message using template or fallback default"""
        try:
            if notification.message:
                template_doc = frappe.get_doc(
                    "WhatsApp Message Template", notification.message
                )
                if (template_doc
                        and template_doc.is_active
                        and template_doc.message_type == "text"
                        and template_doc.template_text):
                    return MessageTemplateHandler._process_template(
                        template_doc.template_text, doc, target_date, target_time
                    )

            return MessageTemplateHandler._build_default_message(
                doc, notification, target_date, target_time
            )
        except Exception as e:
            frappe.log_error(
                f"Error building message: {str(e)}",
                f"WhatsApp Template Error - {doc.doctype}"
            )
            return MessageTemplateHandler._build_fallback_message(
                doc, notification, target_date, target_time
            )

    @staticmethod
    def _process_template(template_text, doc, target_date=None, target_time=None):
        """Replace placeholders in template text"""
        template_text = MessageTemplateHandler.clean_html(template_text)
        placeholders = MessageTemplateHandler._build_placeholders(
            doc, target_date, target_time
        )
        message = template_text

        for placeholder, value in placeholders.items():
            message = message.replace(placeholder, str(value))

        # Handle any remaining {field_name} patterns dynamically
        for pattern in re.findall(r'\{([^}]+)\}', message):
            if hasattr(doc, pattern):
                value = getattr(doc, pattern)
                if value:
                    if isinstance(value, str):
                        value = MessageTemplateHandler.clean_html(value)
                    if hasattr(value, 'strftime'):
                        try:
                            value = formatdate(value)
                        except Exception:
                            value = str(value)
                    message = message.replace(f'{{{pattern}}}', str(value))
                else:
                    message = message.replace(f'{{{pattern}}}', "")

        return MessageTemplateHandler.format_whatsapp_message(message)

    @staticmethod
    def _build_placeholders(doc, target_date=None, target_time=None):
        """Build common replacement dictionary"""
        placeholders = {
            '{doctype}': doc.doctype,
            '{name}': doc.name,
            '{doc_name}': doc.name,
            '{document_name}': doc.name,
        }

        amount = getattr(doc, "grand_total", None) or getattr(doc, "total", None)
        amount_str = f"₹{amount}" if amount else ""
        placeholders.update({
            '{amount}': amount_str,
            '{total}': amount_str,
            '{grand_total}': amount_str
        })

        if target_date:
            placeholders.update({
                '{due_date}': str(target_date),
                '{target_date}': str(target_date),
                '{reminder_date}': str(target_date),
                '{formatted_date}': formatdate(target_date) if target_date else "",
                '{formatted_due_date}': formatdate(target_date) if target_date else ""
            })
            try:
                days_diff = date_diff(target_date, nowdate())
                placeholders.update({
                    '{days_remaining}': str(max(0, days_diff)),
                    '{days_left}': str(max(0, days_diff)),
                    '{day_text}': "day" if days_diff == 1 else "days" if days_diff > 1 else "today"
                })
            except Exception:
                placeholders.update({
                    '{days_remaining}': "", '{days_left}': "", '{day_text}': ""
                })

        if target_time:
            try:
                if isinstance(target_time, str):
                    time_str = target_time
                elif isinstance(target_time, timedelta):
                    total_seconds = int(target_time.total_seconds())
                    hours = total_seconds // 3600
                    minutes = (total_seconds % 3600) // 60
                    time_str = f"{hours:02d}:{minutes:02d}"
                elif hasattr(target_time, 'strftime'):
                    time_str = target_time.strftime("%H:%M")
                else:
                    time_str = str(target_time)

                placeholders.update({
                    '{appointment_time}': time_str,
                    '{target_time}': time_str,
                    '{reminder_time}': time_str,
                    '{scheduled_time}': time_str
                })
            except Exception as e:
                frappe.log_error(
                    f"Error processing time placeholders: {str(e)}",
                    "WhatsApp Time Placeholder Error"
                )
                placeholders.update({
                    '{appointment_time}': "", '{target_time}': "",
                    '{reminder_time}': "", '{scheduled_time}': ""
                })

        common_fields = [
            'customer', 'supplier', 'posting_date', 'due_date',
            'status', 'delivery_date', 'transaction_date',
            'description', 'remarks', 'subject', 'title'
        ]
        for field in common_fields:
            if hasattr(doc, field):
                value = getattr(doc, field)
                if value:
                    if isinstance(value, str) and field in [
                        'description', 'remarks', 'subject', 'title'
                    ]:
                        value = MessageTemplateHandler.clean_html(value)
                    elif hasattr(value, 'strftime'):
                        try:
                            value = formatdate(value)
                        except Exception:
                            value = str(value)
                    placeholders[f'{{{field}}}'] = str(value)
                else:
                    placeholders[f'{{{field}}}'] = ""

        return placeholders

    @staticmethod
    def _build_default_message(doc, notification, target_date=None, target_time=None):
        """Build a sensible default message when no template is configured"""
        if notification.event == "Scheduled Reminder":
            if target_date and target_time:
                try:
                    if isinstance(target_time, str):
                        time_str = target_time
                    elif isinstance(target_time, timedelta):
                        total_seconds = int(target_time.total_seconds())
                        hours = total_seconds // 3600
                        minutes = (total_seconds % 3600) // 60
                        time_str = f"{hours:02d}:{minutes:02d}"
                    elif hasattr(target_time, 'strftime'):
                        time_str = target_time.strftime("%H:%M")
                    else:
                        time_str = str(target_time)
                    message = (
                        f"Reminder: Your {doc.doctype} *{doc.name}* is scheduled "
                        f"for {formatdate(target_date)} at {time_str}."
                    )
                except Exception:
                    message = (
                        f"Reminder: Your {doc.doctype} *{doc.name}* "
                        f"is scheduled for {target_date}."
                    )
            elif target_date:
                message = (
                    f"Reminder: Your {doc.doctype} *{doc.name}* is due on {target_date}."
                )
            else:
                message = (
                    f"Reminder: Your {doc.doctype} *{doc.name}* requires attention."
                )
        else:
            event_messages = {
                "Submit":      f"{doc.doctype} *{doc.name}* has been submitted.",
                "Save":        f"{doc.doctype} *{doc.name}* has been saved.",
                "Cancel":      f"{doc.doctype} *{doc.name}* has been cancelled.",
                "On Creation": f"New {doc.doctype} *{doc.name}* has been created.",
                "On Update":   f"{doc.doctype} *{doc.name}* has been updated."
            }
            message = event_messages.get(
                notification.event,
                f"{doc.doctype} *{doc.name}* has been processed."
            )

        amount = getattr(doc, "grand_total", None) or getattr(doc, "total", None)
        if amount:
            message += f"\nTotal Amount: ₹{amount}"

        return MessageTemplateHandler.format_whatsapp_message(message)

    @staticmethod
    def _build_fallback_message(doc, notification, target_date=None, target_time=None):
        """Bare-minimum fallback"""
        if notification.event == "Scheduled Reminder":
            if target_date:
                message = f"Reminder: {doc.doctype} {doc.name} due on {target_date}."
            else:
                message = f"Reminder: {doc.doctype} {doc.name} requires attention."
        else:
            event_messages = {
                "Cancel":      f"{doc.doctype} {doc.name} has been cancelled.",
                "Submit":      f"{doc.doctype} {doc.name} has been submitted.",
                "Save":        f"{doc.doctype} {doc.name} has been saved.",
                "On Creation": f"New {doc.doctype} {doc.name} has been created.",
                "On Update":   f"{doc.doctype} {doc.name} has been updated."
            }
            message = event_messages.get(
                notification.event,
                f"{doc.doctype} {doc.name} has been processed."
            )
        return MessageTemplateHandler.format_whatsapp_message(message)


# ============================================================================
# NOTIFICATION QUERY HELPERS
# ============================================================================

def get_active_notifications(document_type=None, event=None):
    """Get enabled WhatsApp Notification records with optional filters"""
    filters = {"enabled": 1}
    if document_type:
        filters["document_type"] = document_type
    if event:
        filters["event"] = event
    return frappe.get_all("WhatsApp Notification", filters=filters, fields=["name"])


# ============================================================================
# PHONE NUMBER UTILITIES
# ============================================================================

def get_phone_number(doc, phone_field):
    """Get phone number from document, supporting linked field notation (field.subfield)"""
    if not phone_field:
        return None

    parts = phone_field.strip().split('.')

    if len(parts) == 1:
        phone = getattr(doc, parts[0], None)
        return SparklebotHandler()._clean_phone_number(phone)

    if len(parts) == 2:
        try:
            link_fieldname, target_fieldname = parts
            link_docname = getattr(doc, link_fieldname, None)
            if not link_docname:
                return None

            link_field = doc.meta.get_field(link_fieldname)
            if not link_field or not link_field.options:
                return None

            linked_doc = frappe.get_doc(link_field.options, link_docname)
            phone = getattr(linked_doc, target_fieldname, None)
            return SparklebotHandler()._clean_phone_number(phone)
        except Exception as e:
            frappe.log_error(
                f"Error getting phone number: {str(e)}",
                f"Phone Field Error - {doc.doctype}"
            )
            return None

    return None


def get_phone_number_enhanced(doc, phone_field):
    """Phone getter that also handles user fields (owner, modified_by, etc.)"""
    if not phone_field:
        return None

    phone_field = phone_field.strip()
    user_fields = ['owner', 'modified_by', 'assigned_to', 'created_by',
                   'approved_by', 'submitted_by']

    if phone_field in user_fields:
        return get_user_phone_number(doc, phone_field)

    if '.' in phone_field:
        parts = phone_field.split('.')
        if len(parts) == 2 and parts[0] in user_fields:
            return get_user_phone_number(doc, parts[0])

    return get_phone_number(doc, phone_field)


def get_user_phone_number(doc, user_field):
    """Get phone from a User document field on the doc"""
    try:
        if user_field == "assigned_to":
            assigned_phones = get_assigned_user_phone_numbers(doc)
            if assigned_phones:
                if len(assigned_phones) > 1:
                    frappe.log_error(
                        f"Multiple assignments found ({len(assigned_phones)}), using first.",
                        f"Multiple Assignment Warning - {doc.doctype}"
                    )
                return assigned_phones[0]['phone']
            return None

        username = getattr(doc, user_field, None)
        if not username:
            return None

        user_doc = frappe.get_doc("User", username)
        for field in ['mobile_no', 'phone', 'cell_number', 'whatsapp_number']:
            if hasattr(user_doc, field):
                phone = getattr(user_doc, field)
                if phone:
                    cleaned = SparklebotHandler()._clean_phone_number(phone)
                    if cleaned:
                        return cleaned

        frappe.log_error(
            f"No phone number found for user {username}",
            f"User Phone Lookup - {doc.doctype}"
        )
        return None
    except Exception as e:
        frappe.log_error(
            f"Error getting user phone for {user_field}: {str(e)}",
            f"User Phone Error - {doc.doctype}"
        )
        return None


def get_assigned_user_phone_numbers(doc):
    """Get phone numbers for all open ToDo assignees of a document"""
    try:
        todos = frappe.get_all(
            "ToDo",
            filters={
                "reference_type": doc.doctype,
                "reference_name": doc.name,
                "status": "Open"
            },
            fields=["allocated_to"]
        )

        if not todos:
            todos = frappe.get_all(
                "ToDo",
                filters={"reference_type": doc.doctype, "reference_name": doc.name},
                fields=["allocated_to"]
            )

        if not todos:
            return []

        phone_numbers = []
        processed_users = set()

        for todo in todos:
            if not todo.allocated_to or todo.allocated_to in processed_users:
                continue
            processed_users.add(todo.allocated_to)

            try:
                user_doc = frappe.get_doc("User", todo.allocated_to)
                user_phone = None
                for field in ['mobile_no', 'phone', 'cell_number', 'whatsapp_number']:
                    if hasattr(user_doc, field):
                        phone = getattr(user_doc, field)
                        if phone:
                            cleaned = SparklebotHandler()._clean_phone_number(phone)
                            if cleaned:
                                user_phone = cleaned
                                break

                if user_phone:
                    phone_numbers.append({'phone': user_phone, 'user': todo.allocated_to})
            except Exception as user_error:
                frappe.log_error(
                    f"Error processing user {todo.allocated_to}: {str(user_error)}",
                    f"Assignment User Error - {doc.doctype}"
                )

        return phone_numbers
    except Exception as e:
        frappe.log_error(
            f"Error getting assigned users' phones: {str(e)}",
            f"Assignment Phone Error - {doc.doctype}"
        )
        return []


def get_phone_numbers_by_role(role):
    """Get phone numbers for all enabled users with a specific role"""
    try:
        users = frappe.get_all(
            "Has Role",
            filters={"role": role, "parenttype": "User"},
            fields=["parent"]
        )
        if not users:
            return []

        phone_numbers = []
        processed_users = set()

        for user_data in users:
            username = user_data.parent
            if username in processed_users:
                continue
            processed_users.add(username)

            try:
                user_doc = frappe.get_doc("User", username)
                if user_doc.enabled == 0:
                    continue

                user_phone = None
                for field in ['mobile_no', 'phone', 'cell_number', 'whatsapp_number']:
                    if hasattr(user_doc, field):
                        phone = getattr(user_doc, field)
                        if phone:
                            cleaned = SparklebotHandler()._clean_phone_number(phone)
                            if cleaned:
                                user_phone = cleaned
                                break

                if user_phone:
                    phone_numbers.append({'phone': user_phone, 'user': username})
            except Exception as user_error:
                frappe.log_error(
                    f"Error processing user {username}: {str(user_error)}",
                    f"Role User Error - {role}"
                )

        return phone_numbers
    except Exception as e:
        frappe.log_error(
            f"Error getting users by role {role}: {str(e)}",
            "Role Phone Error"
        )
        return []


# ============================================================================
# LINKED DOCUMENT PROCESSING
# ============================================================================

def find_linked_document(doc, target_doctype):
    """Scan Link fields on doc to find one pointing to target_doctype"""
    try:
        meta = frappe.get_meta(doc.doctype)

        for field in meta.fields:
            if field.fieldtype == "Link" and field.options == target_doctype:
                value = getattr(doc, field.fieldname, None)
                if value:
                    return value

        for field in meta.fields:
            if field.fieldtype == "Dynamic Link":
                link_doctype_field = field.options
                if link_doctype_field and hasattr(doc, link_doctype_field):
                    actual_doctype = getattr(doc, link_doctype_field, None)
                    if actual_doctype == target_doctype:
                        value = getattr(doc, field.fieldname, None)
                        if value:
                            return value

        return None
    except Exception as e:
        frappe.log_error(
            f"Error finding linked document: {str(e)}",
            f"WhatsApp Find Link Error - {doc.doctype}"
        )
        return None


def get_linked_document_recipients(doc, notification):
    """Gather phone numbers from linked documents configured in the notification"""
    all_phones = []

    if not hasattr(notification, 'linked_documents') or not notification.linked_documents:
        return all_phones

    for linked_config in notification.linked_documents:
        try:
            linked_doctype = linked_config.linked_doctype
            if not linked_doctype:
                continue

            linked_doc_name = find_linked_document(doc, linked_doctype)
            if not linked_doc_name:
                frappe.log_error(
                    f"No link to {linked_doctype} found in {doc.doctype} {doc.name}",
                    f"WhatsApp Linked Doc - {doc.doctype}"
                )
                continue

            try:
                linked_doc = frappe.get_doc(linked_doctype, linked_doc_name)
            except Exception as e:
                frappe.log_error(
                    f"Could not fetch linked doc {linked_doctype} {linked_doc_name}: {str(e)}",
                    f"WhatsApp Linked Doc Error - {doc.doctype}"
                )
                continue

            if linked_config.condition:
                if not evaluate_custom_condition(linked_doc, linked_config.condition):
                    continue

            if linked_config.phone_field:
                phone = get_phone_number_enhanced(linked_doc, linked_config.phone_field)
                if phone:
                    all_phones.append({
                        'phone': phone,
                        'source': (
                            f"Linked {linked_doctype}: {linked_doc_name} "
                            f"({linked_config.phone_field})"
                        )
                    })

            if linked_config.send_to_all_assignees:
                for assigned in get_assigned_user_phone_numbers(linked_doc):
                    all_phones.append({
                        'phone': assigned['phone'],
                        'source': f"Linked {linked_doctype} Assignee: {assigned['user']}"
                    })

            if linked_config.receiver_by_role:
                for role_phone in get_phone_numbers_by_role(linked_config.receiver_by_role):
                    all_phones.append({
                        'phone': role_phone['phone'],
                        'source': (
                            f"Linked {linked_doctype} Role "
                            f"({linked_config.receiver_by_role}): {role_phone['user']}"
                        )
                    })

        except Exception as e:
            frappe.log_error(
                f"Error processing linked document config: {str(e)}",
                f"WhatsApp Linked Doc Error - {doc.doctype}"
            )

    return all_phones


# ============================================================================
# RECIPIENT PROCESSING
# ============================================================================

def process_notification_recipients(doc, notification):
    """
    Aggregate all recipient phone numbers from:
      - send_to_all_assignees flag
      - linked_documents child table
      - recipients child table (document field / role)

    Returns list of {"phone": str, "source": str}
    """
    all_phones = []

    if getattr(notification, 'send_to_all_assignees', False):
        for assigned in get_assigned_user_phone_numbers(doc):
            all_phones.append({
                'phone': assigned['phone'],
                'source': f"Assigned User: {assigned['user']}"
            })

    linked_phones = get_linked_document_recipients(doc, notification)
    all_phones.extend(linked_phones)

    if not notification.recipients:
        return _deduplicate(all_phones)

    for recipient in notification.recipients:
        try:
            if recipient.condition:
                if not evaluate_custom_condition(doc, recipient.condition):
                    continue

            if recipient.receiver_by_document_field:
                phone = get_phone_number_enhanced(doc, recipient.receiver_by_document_field)
                if phone:
                    all_phones.append({
                        'phone': phone,
                        'source': f"Field: {recipient.receiver_by_document_field}"
                    })
                else:
                    frappe.log_error(
                        f"No phone for field '{recipient.receiver_by_document_field}' "
                        f"in {doc.name}",
                        f"WhatsApp Recipient Phone - {doc.doctype}"
                    )

            if recipient.receiver_by_role:
                role_phones = get_phone_numbers_by_role(recipient.receiver_by_role)
                for rp in role_phones:
                    all_phones.append({
                        'phone': rp['phone'],
                        'source': f"Role: {recipient.receiver_by_role} - User: {rp['user']}"
                    })
                if not role_phones:
                    frappe.log_error(
                        f"No users with phone found for role '{recipient.receiver_by_role}'",
                        f"WhatsApp Role Phone - {doc.doctype}"
                    )
        except Exception as e:
            frappe.log_error(
                f"Error processing recipient: {str(e)}",
                f"WhatsApp Recipient Error - {doc.doctype}"
            )

    return _deduplicate(all_phones)


def _deduplicate(phone_list):
    """Remove duplicate phones while preserving order"""
    seen = set()
    result = []
    for item in phone_list:
        if item['phone'] not in seen:
            seen.add(item['phone'])
            result.append(item)
    return result


# ============================================================================
# CONDITION EVALUATION
# ============================================================================

def evaluate_custom_condition(doc, condition_code):
    """Safely evaluate a Python condition string against a document"""
    if not condition_code or not condition_code.strip():
        return True

    try:
        context = {
            'doc': doc,
            'frappe': frappe,
            'nowdate': frappe.utils.nowdate,
            'now_datetime': frappe.utils.now_datetime,
            'add_days': frappe.utils.add_days,
            'date_diff': frappe.utils.date_diff,
            'cint': frappe.utils.cint,
            'cstr': frappe.utils.cstr,
            'flt': frappe.utils.flt
        }

        try:
            for fieldname in doc.meta.get_valid_columns():
                if hasattr(doc, fieldname):
                    context[fieldname] = doc.get(fieldname)
        except Exception:
            pass

        try:
            for key in doc.as_dict():
                context[key] = doc.get(key)
        except Exception:
            pass

        return bool(frappe.safe_eval(condition_code, context))
    except Exception as e:
        frappe.log_error(
            f"Error evaluating condition: {str(e)}\n"
            f"Condition: {condition_code}\nDocument: {doc.name}",
            f"WhatsApp Condition Error - {doc.doctype}"
        )
        return False


# ============================================================================
# CORE NOTIFICATION PROCESSOR
# ============================================================================

def _dispatch_message(handler, recipient, doc, notification,
                      target_date=None, target_time=None):
    """
    Send one message to one recipient.
    Determines text vs template mode from the notification's message template.
    """
    phone = recipient['phone']

    template_doc = None
    if notification.message:
        try:
            template_doc = frappe.get_doc("WhatsApp Message Template", notification.message)
        except Exception:
            pass

    # Template mode
    if (template_doc
            and template_doc.is_active
            and template_doc.message_type == "template"
            and template_doc.wa_template_name):
        field_params = TemplateParameterBuilder.build(template_doc, doc)
        header_url = None
        if template_doc.header_document_field:
            header_url = doc.get(template_doc.header_document_field) or None
        return handler.send_template(
            phone,
            template_doc.wa_template_name,
            template_doc.template_language or "en",
            field_params,
            doc.doctype,
            doc.name,
            header_document_url=header_url
        )

    # Text mode (default)
    message = MessageTemplateHandler.build_message(
        doc, notification,
        is_reminder=(notification.event == "Scheduled Reminder"),
        target_date=target_date,
        target_time=target_time
    )
    return handler.send_text(phone, message, doc.doctype, doc.name)


def _process_whatsapp_notification(doc, notification, handler,
                                   target_date=None, target_time=None):
    """Process one notification: gather recipients and dispatch messages"""
    recipients = process_notification_recipients(doc, notification)

    if not recipients:
        frappe.log_error(
            title=f"WhatsApp No Recipients - {doc.doctype}",
            message=f"Notification: {notification.name} | Document: {doc.name}"
        )
        return

    _logger = frappe.logger("whatsapp")
    success_count = error_count = 0

    for recipient in recipients:
        try:
            if _dispatch_message(handler, recipient, doc, notification,
                                  target_date, target_time):
                success_count += 1
                _logger.info(
                    f"WhatsApp sent | {doc.doctype} {doc.name} | {recipient['phone']}"
                )
            else:
                error_count += 1
        except Exception as send_error:
            error_count += 1
            frappe.log_error(
                title=f"WhatsApp Send Error - {doc.doctype}",
                message=f"Notification: {notification.name}\nRecipient: {recipient['source']}\n{str(send_error)}"
            )

    _logger.info(
        f"WhatsApp summary | {notification.name} | {doc.doctype} {doc.name} "
        f"| sent:{success_count} failed:{error_count}"
    )


# ============================================================================
# DOCUMENT EVENT HANDLERS
# ============================================================================

# Doctypes that should never trigger WhatsApp handlers (prevents recursion / noise)
_EXCLUDED_DOCTYPES = frozenset([
    'WhatsApp Notification', 'WhatsApp Notification Recipient',
    'WhatsApp Message Template', 'WhatsApp Template Parameter',
    'WhatsApp Setting', 'WhatsApp Linked Document',
    'Comment', 'Communication', 'Email Queue', 'Notification Log',
    'Activity Log', 'Error Log', 'Scheduled Job Log', 'Version',
    'Access Log', 'Route History', 'View Log', 'Energy Point Log',
    'Notification Settings', 'Web Form', 'Web Page', 'Portal Settings'
])


def _run_whatsapp_notification_bg(doctype, docname, trigger_event):
    """RQ worker: reloads the document from DB and runs WhatsApp notification logic."""
    try:
        doc = frappe.get_doc(doctype, docname)
        _handle_whatsapp_notification(doc, None, trigger_event)
    except Exception as e:
        frappe.log_error(
            title=f"WhatsApp BG Worker Error - {doctype}",
            message=f"DocType: {doctype} | Name: {docname} | Event: {trigger_event}\n{str(e)}"
        )


def _enqueue_whatsapp_notification(doc, trigger_event):
    # Gate before enqueueing — excluded doctypes have ephemeral records that
    # may not exist by the time the RQ worker runs (e.g. Version, Activity Log).
    if doc.doctype in _EXCLUDED_DOCTYPES:
        return
    frappe.enqueue(
        "techniti.whatsapp.whatsapp._run_whatsapp_notification_bg",
        queue="short",
        timeout=120,
        doctype=doc.doctype,
        docname=doc.name,
        trigger_event=trigger_event,
    )


def handle_whatsapp_notification_submit(doc, method):
    _enqueue_whatsapp_notification(doc, "Submit")


def handle_whatsapp_notification_save(doc, method):
    _enqueue_whatsapp_notification(doc, "Save")


def handle_whatsapp_notification_cancel(doc, method):
    _enqueue_whatsapp_notification(doc, "Cancel")


def handle_whatsapp_notification_creation(doc, method):
    _enqueue_whatsapp_notification(doc, "On Creation")


def handle_whatsapp_notification_update(doc, method):
    if doc.is_new():
        return
    _enqueue_whatsapp_notification(doc, "On Update")


def _handle_whatsapp_notification(doc, method, trigger_event):
    """Unified handler for immediate document events"""
    try:
        if doc.doctype in _EXCLUDED_DOCTYPES:
            return

        settings = safe_get_settings()
        if not settings or not settings.enabled:
            return

        notifications = get_active_notifications(
            document_type=doc.doctype,
            event=trigger_event
        )
        if not notifications:
            return

        handler = SparklebotHandler()

        for notification_data in notifications:
            try:
                notification = frappe.get_doc(
                    "WhatsApp Notification", notification_data.name
                )
                if notification.condition:
                    if not evaluate_custom_condition(doc, notification.condition):
                        continue

                _process_whatsapp_notification(doc, notification, handler)

            except Exception as e:
                frappe.log_error(
                    title=f"WhatsApp Notification Error - {doc.doctype}",
                    message=f"Notification: {notification_data.name}\n{str(e)}"
                )

    except Exception as e:
        frappe.log_error(
            title="WhatsApp Handler Failed",
            message=f"DocType: {doc.doctype} | Name: {doc.name}\n{str(e)}"
        )


# ============================================================================
# SCHEDULED REMINDERS — DATE-BASED (daily cron)
# ============================================================================

def send_scheduled_whatsapp_reminders_enhanced():
    """Run daily: sends date-based reminder notifications"""
    settings = safe_get_settings()
    if not settings or not settings.enabled:
        return

    notifications = frappe.get_all(
        "WhatsApp Notification",
        filters={"enabled": 1, "event": "Scheduled Reminder", "add_timing": 0},
        fields=["name"]
    )

    for notification_data in notifications:
        try:
            notification = frappe.get_doc(
                "WhatsApp Notification", notification_data.name
            )
            if not notification.date_field:
                frappe.log_error(
                    f"No date_field configured for notification {notification.name}",
                    "WhatsApp Reminder Config Error"
                )
                continue

            if '.' in notification.date_field:
                process_child_table_reminders(notification)
            else:
                process_document_reminders(notification)

        except Exception as e:
            frappe.log_error(
                f"Scheduler failed for {notification_data.name}: {str(e)}",
                "WhatsApp Scheduler Error"
            )


def process_document_reminders(notification):
    """Process document-level date-based reminders"""
    date_field = notification.date_field

    if notification.days_before:
        target_date = add_days(nowdate(), notification.days_before)
    elif notification.days_after:
        target_date = add_days(nowdate(), -(notification.days_after))
    else:
        target_date = nowdate()

    filters = {date_field: target_date}

    submittable_doctypes = [
        "Purchase Order", "Sales Order", "Purchase Invoice",
        "Sales Invoice", "Delivery Note", "Purchase Receipt"
    ]
    if notification.document_type in submittable_doctypes:
        filters["docstatus"] = ["!=", 2]

    try:
        docs = frappe.get_all(
            notification.document_type,
            filters=filters,
            fields=["name"]
        )
    except Exception as e:
        frappe.log_error(
            f"Error querying documents: {str(e)}",
            f"WhatsApp Reminder Query - {notification.document_type}"
        )
        return

    handler = SparklebotHandler()
    success_count = error_count = condition_skip_count = 0

    for d in docs:
        try:
            doc = frappe.get_doc(notification.document_type, d.name)

            if hasattr(doc, 'docstatus') and doc.docstatus == 2:
                continue

            if notification.condition:
                if not evaluate_custom_condition(doc, notification.condition):
                    condition_skip_count += 1
                    continue

            recipients = process_notification_recipients(doc, notification)
            if not recipients:
                error_count += 1
                continue

            for recipient in recipients:
                try:
                    if _dispatch_message(handler, recipient, doc, notification,
                                          target_date=target_date):
                        success_count += 1
                    else:
                        error_count += 1
                except Exception as send_error:
                    error_count += 1
                    frappe.log_error(
                        f"Send failed to {recipient['source']}: {str(send_error)}",
                        "WhatsApp Reminder Send Error"
                    )

        except Exception as e:
            frappe.log_error(
                f"Failed to process reminder for {d.name}: {str(e)}",
                "WhatsApp Reminder Error"
            )
            error_count += 1

    if success_count or error_count or condition_skip_count:
        frappe.log_error(
            f"Reminders for {notification.name} — "
            f"Sent: {success_count}, Errors: {error_count}, "
            f"Skipped: {condition_skip_count}",
            f"WhatsApp Reminder Summary - {notification.document_type}"
        )


def process_child_table_reminders(notification):
    """Process child-table date-based reminders (date_field uses 'table_field.date_field')"""
    date_field = notification.date_field

    if '.' not in date_field:
        frappe.log_error(
            "Invalid child table field format. Use 'table_field.date_field'",
            "WhatsApp Child Reminder Config Error"
        )
        return

    table_fieldname, child_date_field = date_field.split('.', 1)

    if notification.days_before:
        target_date = add_days(nowdate(), notification.days_before)
    elif notification.days_after:
        target_date = add_days(nowdate(), -(notification.days_after))
    else:
        target_date = nowdate()

    try:
        # Find parent docs that have a child row matching target_date
        child_doctype = frappe.get_meta(notification.document_type)\
                               .get_field(table_fieldname)\
                               .options if frappe.get_meta(
                                   notification.document_type
                               ).get_field(table_fieldname) else None

        if not child_doctype:
            frappe.log_error(
                f"Could not find child doctype for field {table_fieldname} "
                f"in {notification.document_type}",
                "WhatsApp Child Reminder Error"
            )
            return

        parent_names = frappe.get_all(
            child_doctype,
            filters={child_date_field: target_date},
            fields=["parent"],
            distinct=True
        )

        handler = SparklebotHandler()
        success_count = error_count = 0

        for row in parent_names:
            try:
                doc = frappe.get_doc(notification.document_type, row.parent)

                if hasattr(doc, 'docstatus') and doc.docstatus == 2:
                    continue

                if notification.condition:
                    if not evaluate_custom_condition(doc, notification.condition):
                        continue

                recipients = process_notification_recipients(doc, notification)
                if not recipients:
                    error_count += 1
                    continue

                for recipient in recipients:
                    if _dispatch_message(handler, recipient, doc, notification,
                                          target_date=target_date):
                        success_count += 1
                    else:
                        error_count += 1

            except Exception as e:
                frappe.log_error(
                    f"Child reminder error for {row.parent}: {str(e)}",
                    "WhatsApp Child Reminder Error"
                )
                error_count += 1

        if success_count or error_count:
            frappe.log_error(
                f"Child reminders for {notification.name} — "
                f"Sent: {success_count}, Errors: {error_count}",
                f"WhatsApp Child Reminder Summary - {notification.document_type}"
            )

    except Exception as e:
        frappe.log_error(
            f"Child table reminder failed for {notification.name}: {str(e)}",
            "WhatsApp Child Reminder Error"
        )


# ============================================================================
# SCHEDULED REMINDERS — TIME-BASED (hourly cron)
# ============================================================================

def process_scheduled_whatsapp_time_reminders():
    """Run hourly: sends time-based reminder notifications"""
    settings = safe_get_settings()
    if not settings or not settings.enabled:
        return

    notifications = frappe.get_all(
        "WhatsApp Notification",
        filters={"enabled": 1, "event": "Scheduled Reminder", "add_timing": 1},
        fields=["name"]
    )
    if not notifications:
        return

    for notification_data in notifications:
        try:
            notification = frappe.get_doc(
                "WhatsApp Notification", notification_data.name
            )
            if not notification.time_field or not notification.hours_before:
                frappe.log_error(
                    f"Incomplete time config for {notification.name}",
                    "WhatsApp Time Config Error"
                )
                continue

            process_time_based_reminders(notification)

        except Exception as e:
            frappe.log_error(
                f"Time reminder failed for {notification_data.name}: {str(e)}",
                "WhatsApp Time Reminder Error"
            )


def process_time_based_reminders(notification):
    """Process one time-based notification"""
    current_datetime = now_datetime()
    current_date = current_datetime.date()

    target_start_time = current_datetime
    target_end_time = add_to_date(current_datetime, hours=notification.hours_before)

    date_filters = {notification.date_field: current_date}
    submittable_doctypes = [
        "Purchase Order", "Sales Order", "Purchase Invoice",
        "Sales Invoice", "Delivery Note", "Purchase Receipt"
    ]
    if notification.document_type in submittable_doctypes:
        date_filters["docstatus"] = ["!=", 2]

    try:
        docs = frappe.get_all(
            notification.document_type,
            filters=date_filters,
            fields=["name", notification.time_field]
        )
    except Exception as e:
        frappe.log_error(
            f"Error querying documents: {str(e)}",
            f"WhatsApp Time Query Error - {notification.document_type}"
        )
        return

    handler = SparklebotHandler()
    success_count = error_count = condition_skip_count = time_skip_count = 0

    for d in docs:
        try:
            doc = frappe.get_doc(notification.document_type, d.name)

            if hasattr(doc, 'docstatus') and doc.docstatus == 2:
                continue

            time_field_value = getattr(doc, notification.time_field, None)
            if not time_field_value:
                time_skip_count += 1
                continue

            if not is_time_in_range(time_field_value, target_start_time, target_end_time):
                time_skip_count += 1
                continue

            if notification.condition:
                if not evaluate_custom_condition(doc, notification.condition):
                    condition_skip_count += 1
                    continue

            recipients = process_notification_recipients(doc, notification)
            if not recipients:
                error_count += 1
                continue

            for recipient in recipients:
                try:
                    if _dispatch_message(handler, recipient, doc, notification,
                                          target_date=current_date,
                                          target_time=time_field_value):
                        success_count += 1
                    else:
                        error_count += 1
                except Exception as send_error:
                    error_count += 1
                    frappe.log_error(
                        f"Send failed to {recipient['source']}: {str(send_error)}",
                        "WhatsApp Time Send Error"
                    )

        except Exception as e:
            frappe.log_error(
                f"Failed to process time reminder for {d.name}: {str(e)}",
                f"WhatsApp Time Processing Error - {notification.document_type}"
            )
            error_count += 1

    if success_count or error_count or condition_skip_count or time_skip_count:
        frappe.log_error(
            f"Time reminders for {notification.name} — "
            f"Sent: {success_count}, Errors: {error_count}, "
            f"Condition Skips: {condition_skip_count}, Time Skips: {time_skip_count}",
            f"WhatsApp Time Summary - {notification.document_type}"
        )


def is_time_in_range(time_field_value, start_datetime, end_datetime):
    """Check if a time value falls within [start_datetime, end_datetime]"""
    try:
        if isinstance(time_field_value, datetime):
            appointment_datetime = time_field_value
        elif isinstance(time_field_value, time):
            appointment_datetime = datetime.combine(
                start_datetime.date(), time_field_value
            )
        elif isinstance(time_field_value, timedelta):
            midnight = datetime.combine(start_datetime.date(), time.min)
            appointment_datetime = midnight + time_field_value
        elif isinstance(time_field_value, str):
            try:
                parsed_time = datetime.strptime(time_field_value, "%H:%M").time()
                appointment_datetime = datetime.combine(
                    start_datetime.date(), parsed_time
                )
            except ValueError:
                try:
                    appointment_datetime = datetime.strptime(
                        time_field_value, "%Y-%m-%d %H:%M:%S"
                    )
                except ValueError:
                    parts = time_field_value.split(':')
                    if len(parts) >= 2:
                        h, m = int(parts[0]), int(parts[1])
                        s = int(parts[2]) if len(parts) > 2 else 0
                        appointment_datetime = datetime.combine(
                            start_datetime.date(), time(h, m, s)
                        )
                    else:
                        return False
        else:
            return False

        return start_datetime <= appointment_datetime <= end_datetime

    except Exception as e:
        frappe.log_error(f"Error checking time range: {str(e)}", "WhatsApp Time Range Error")
        return False
