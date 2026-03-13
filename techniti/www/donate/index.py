# Copyright (c) 2024, TechNiti and contributors
# For license information, please see license.txt

import frappe

no_cache = 1
sitemap = 1


def get_context(context):
	context.no_cache = 1

	# Require login — redirect guests to donor login page
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/donor-login"
		raise frappe.Redirect

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

	# Get causes marked show_on_website only
	context.causes = frappe.get_all(
		"Cause",
		filters={"show_on_website": 1},
		fields=["name", "cause", "display_order"],
		order_by="display_order asc, cause asc"
	)

	# Get Razorpay settings
	context.razorpay_key_id = frappe.db.get_single_value("Website Donation Settings", "razorpay_key_id") or ""

	# Donation settings for JS
	settings = frappe.get_single("Website Donation Settings")
	context.allow_donor_amount_change = settings.get("allow_donor_amount_change") or 0

	# Real stats — rounded down to nearest 100 with "+" suffix
	stats = frappe.db.sql("""
		SELECT COUNT(DISTINCT donor) as total_donors,
		       COALESCE(SUM(amount), 0) as total_amount
		FROM `tabWebsite Donation`
		WHERE payment_status = 'Captured'
	""", as_dict=True)
	total_donors = int((stats[0].total_donors or 0)) if stats else 0
	rounded = (total_donors // 100) * 100
	context.donor_count_display = f"{rounded}+" if rounded > 0 else (f"{total_donors}+" if total_donors > 0 else "10+")

	# If logged-in donor, pre-fill their details
	context.logged_in_donor = None
	if frappe.session.user and frappe.session.user != "Guest":
		donor_name = frappe.db.get_value("Website Donor", {"linked_user": frappe.session.user}, "name")
		if donor_name:
			context.logged_in_donor = frappe.db.get_value(
				"Website Donor", donor_name,
				["name", "full_name", "email", "mobile", "id_type", "id_number",
				 "is_club_donor", "subscription_status"],
				as_dict=True
			)

	return context
