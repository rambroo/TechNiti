# Copyright (c) 2024, TechNiti and contributors
# For license information, please see license.txt

import frappe

no_cache = 1
sitemap = 1


def get_context(context):
	context.no_cache = 1

	# Get active campaigns
	campaigns = frappe.get_all(
		"Website Donation Campaign",
		filters={
			"status": "Active",
			"show_on_website": 1
		},
		fields=[
			"name", "campaign_name", "description", "campaign_image",
			"target_amount", "collected_amount", "donor_count",
			"minimum_amount", "suggested_amounts", "allow_any_amount",
			"is_default"
		],
		order_by="is_default desc, creation desc"
	)

	context.campaigns = campaigns

	# Get default campaign or first campaign
	context.default_campaign = None
	for campaign in campaigns:
		if campaign.is_default:
			context.default_campaign = campaign
			break
	if not context.default_campaign and campaigns:
		context.default_campaign = campaigns[0]

	# Get Razorpay settings
	context.razorpay_key_id = frappe.db.get_single_value("Website Donation Settings", "razorpay_key_id") or ""

	return context
