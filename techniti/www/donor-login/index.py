import frappe


def get_context(context):
	context.no_cache = 1

	# If already logged in as a donor, redirect to portal
	if frappe.session.user and frappe.session.user != "Guest":
		donor = frappe.db.get_value("Website Donor", {"linked_user": frappe.session.user}, "name")
		if donor:
			frappe.local.flags.redirect_location = "/donor-portal"
			raise frappe.Redirect

	context.title = "Donor Login"
