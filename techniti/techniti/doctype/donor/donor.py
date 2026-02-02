# Copyright (c) 2024, TechNiti and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class Donor(Document):
	def before_save(self):
		self.update_donation_stats()

	def update_donation_stats(self):
		"""Update total donated amount and donation count"""
		donations = frappe.get_all(
			"Donation",
			filters={"donor": self.name, "payment_status": "Paid"},
			fields=["sum(amount) as total", "count(*) as count"]
		)
		if donations:
			self.total_donated = donations[0].total or 0
			self.donation_count = donations[0].count or 0
