# Copyright (c) 2024, TechNiti and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class WebsiteDonationCampaign(Document):
	def validate(self):
		self.validate_dates()
		self.validate_default_campaign()

	def validate_dates(self):
		if self.end_date and self.start_date and self.end_date < self.start_date:
			frappe.throw("End Date cannot be before Start Date")

	def validate_default_campaign(self):
		if self.is_default:
			# Unset other default campaigns
			frappe.db.sql("""
				UPDATE `tabWebsite Donation Campaign`
				SET is_default = 0
				WHERE name != %s AND is_default = 1
			""", self.name)

	def before_save(self):
		self.update_collection_stats()

	def update_collection_stats(self):
		"""Update collected amount and donor count"""
		result = frappe.db.sql("""
			SELECT
				COALESCE(SUM(amount), 0) as total,
				COUNT(DISTINCT donor) as donors
			FROM `tabWebsite Donation`
			WHERE campaign = %s AND payment_status = 'Paid'
		""", self.name, as_dict=True)

		if result:
			self.collected_amount = result[0].total
			self.donor_count = result[0].donors

	@staticmethod
	def get_active_campaigns():
		"""Get all active campaigns for website"""
		return frappe.get_all(
			"Website Donation Campaign",
			filters={
				"status": "Active",
				"show_on_website": 1
			},
			fields=[
				"name", "campaign_name", "description", "campaign_image",
				"target_amount", "collected_amount", "donor_count",
				"minimum_amount", "suggested_amounts", "allow_any_amount"
			],
			order_by="is_default desc, creation desc"
		)
