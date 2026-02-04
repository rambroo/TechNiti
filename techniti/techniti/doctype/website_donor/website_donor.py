# Copyright (c) 2024, TechNiti and contributors
# For license information, please see license.txt

import re
import frappe
from frappe.model.document import Document


class WebsiteDonor(Document):
	def validate(self):
		self.validate_id_number()

	def validate_id_number(self):
		if self.id_type and self.id_number:
			if self.id_type == "PAN Card":
				self.id_number = self.id_number.strip().upper()
				if not re.match(r'^[A-Z]{5}[0-9]{4}[A-Z]{1}$', self.id_number):
					frappe.throw("Please enter a valid PAN number (e.g., ABCDE1234F)")
			elif self.id_type == "Aadhar Card":
				self.id_number = self.id_number.strip().replace(" ", "")
				if not re.match(r'^\d{12}$', self.id_number):
					frappe.throw("Please enter a valid 12-digit Aadhar number")

	def before_save(self):
		self.update_donation_stats()

	def update_donation_stats(self):
		"""Update total donated amount and donation count"""
		donations = frappe.get_all(
			"Website Donation",
			filters={"donor": self.name, "payment_status": "Paid"},
			fields=["sum(amount) as total", "count(*) as count"]
		)
		if donations:
			self.total_donated = donations[0].total or 0
			self.donation_count = donations[0].count or 0
