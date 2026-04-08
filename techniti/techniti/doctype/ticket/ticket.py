# Copyright (c) 2026, Rohan Rambhiya and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class Ticket(Document):
	def validate(self):
		if not self.confirmation:
			frappe.throw("Please confirm that the details provided are correct before submitting.")
