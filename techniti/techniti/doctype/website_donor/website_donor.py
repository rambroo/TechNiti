# Copyright (c) 2024, TechNiti and contributors
# For license information, please see license.txt

import re
import frappe
from frappe import _
from frappe.model.document import Document


class WebsiteDonor(Document):

	def validate(self):
		if self.full_name:
			self.full_name = self.full_name.strip().upper()
		self.validate_id_number()
		self.check_duplicate_id()

	def validate_id_number(self):
		if not self.id_type or not self.id_number:
			return
		if self.id_type == "PAN Card":
			self.id_number = self.id_number.strip().upper()
			if not re.match(r'^[A-Z]{5}[0-9]{4}[A-Z]{1}$', self.id_number):
				frappe.throw(_("Invalid PAN Card number. Format: ABCDE1234F"))
		elif self.id_type == "Aadhar Card":
			self.id_number = self.id_number.strip().replace(" ", "")
			if not re.match(r'^[2-9]\d{11}$', self.id_number):
				frappe.throw(_("Invalid Aadhar number. Must be 12 digits starting with 2-9."))

	def check_duplicate_id(self):
		if not self.id_type or not self.id_number:
			return
		existing = frappe.db.get_value(
			"Website Donor",
			{"id_type": self.id_type, "id_number": self.id_number, "name": ["!=", self.name or ""]},
			"name"
		)
		if existing:
			frappe.throw(_("A donor with this {0} ({1}) already exists: {2}").format(
				self.id_type, self.id_number, existing))

	def before_save(self):
		self.update_donation_stats()

	def after_insert(self):
		if self.email:
			self._create_portal_user()

	def _create_portal_user(self):
		"""Create a Frappe User for portal login"""
		if not self.email:
			return
		if frappe.db.exists("User", self.email):
			# Link existing user
			frappe.db.set_value("Website Donor", self.name, "linked_user", self.email, update_modified=False)
			return
		try:
			# Ensure the Website Donor role exists
			if not frappe.db.exists("Role", "Website Donor"):
				role_doc = frappe.get_doc({"doctype": "Role", "role_name": "Website Donor", "desk_access": 0})
				role_doc.insert(ignore_permissions=True)

			user = frappe.get_doc({
				"doctype": "User",
				"email": self.email,
				"first_name": self.full_name.title() if self.full_name else "",
				"user_type": "Website User",
				"send_welcome_email": 0,
				"roles": [{"role": "Website Donor"}]
			})
			user.insert(ignore_permissions=True)
			frappe.db.set_value("Website Donor", self.name, "linked_user", user.name, update_modified=False)
		except Exception as e:
			frappe.log_error(f"Failed to create user for donor {self.name}: {str(e)}", "Donor User Creation")

	def update_donation_stats(self):
		"""Update total donated amount, donation count and last donation date"""
		stats = frappe.db.sql("""
			SELECT COUNT(*) as cnt, COALESCE(SUM(amount), 0) as total, MAX(donation_date) as last_date
			FROM `tabWebsite Donation`
			WHERE donor = %s AND payment_status = 'Captured'
		""", self.name, as_dict=True)

		if stats:
			self.total_donated = stats[0].total or 0
			self.donation_count = stats[0].cnt or 0
			self.last_donation_date = stats[0].last_date
