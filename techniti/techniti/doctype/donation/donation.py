# Copyright (c) 2024, TechNiti and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class Donation(Document):
	def validate(self):
		self.validate_amount()
		self.set_donor_details()

	def validate_amount(self):
		if self.amount <= 0:
			frappe.throw("Donation amount must be greater than 0")

		if self.campaign:
			campaign = frappe.get_doc("Donation Campaign", self.campaign)
			if campaign.minimum_amount and self.amount < campaign.minimum_amount:
				frappe.throw(f"Minimum donation amount is {campaign.minimum_amount}")

	def set_donor_details(self):
		if self.donor:
			donor = frappe.get_doc("Donor", self.donor)
			self.donor_name = donor.full_name
			self.donor_email = donor.email
			self.donor_mobile = donor.mobile

	def on_submit(self):
		self.update_donor_stats()
		self.update_campaign_stats()

	def on_cancel(self):
		self.update_donor_stats()
		self.update_campaign_stats()

	def update_donor_stats(self):
		if self.donor:
			donor = frappe.get_doc("Donor", self.donor)
			donor.update_donation_stats()
			donor.save(ignore_permissions=True)

	def update_campaign_stats(self):
		if self.campaign:
			campaign = frappe.get_doc("Donation Campaign", self.campaign)
			campaign.update_collection_stats()
			campaign.save(ignore_permissions=True)

	def on_payment_success(self, payment_id, order_id, signature, payment_method=None):
		"""Called when payment is successful"""
		self.razorpay_payment_id = payment_id
		self.razorpay_order_id = order_id
		self.razorpay_signature = signature
		self.payment_status = "Paid"
		if payment_method:
			self.payment_method = payment_method
		self.save(ignore_permissions=True)
		self.submit()

	def on_payment_failure(self):
		"""Called when payment fails"""
		self.payment_status = "Failed"
		self.save(ignore_permissions=True)
