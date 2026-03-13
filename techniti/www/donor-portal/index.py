import frappe


def get_context(context):
	context.no_cache = 1

	# Must be logged in
	if not frappe.session.user or frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/donor-login"
		raise frappe.Redirect

	# Must have a linked donor account
	donor_name = frappe.db.get_value("Website Donor", {"linked_user": frappe.session.user}, "name")
	if not donor_name:
		frappe.local.flags.redirect_location = "/donor-login"
		raise frappe.Redirect

	context.title = "Donor Portal"
	context.donor_name = donor_name
