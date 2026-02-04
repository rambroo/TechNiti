# Copyright (c) 2024, TechNiti and contributors
# For license information, please see license.txt

import frappe

no_cache = 1


def get_context(context):
	context.no_cache = 1

	donation_id = frappe.form_dict.get("id")

	if donation_id:
		try:
			donation = frappe.get_doc("Website Donation", donation_id)
			context.donation = donation
			context.success = donation.payment_status == "Paid"
		except frappe.DoesNotExistError:
			context.donation = None
			context.success = False
	else:
		context.donation = None
		context.success = False

	return context
