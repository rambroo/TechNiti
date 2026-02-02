app_name = "techniti"
app_title = "TechNiti"
app_publisher = "Rohan Rambhiya"
app_description = "Donation Software"
app_email = "websitecreateionbyrohan@gmail.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "techniti",
# 		"logo": "/assets/techniti/logo.png",
# 		"title": "TechNiti",
# 		"route": "/techniti",
# 		"has_permission": "techniti.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/techniti/css/techniti.css"
# app_include_js = "/assets/techniti/js/techniti.js"

# include js, css files in header of web template
# web_include_css = "/assets/techniti/css/techniti.css"
# web_include_js = "/assets/techniti/js/techniti.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "techniti/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "techniti/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "techniti.utils.jinja_methods",
# 	"filters": "techniti.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "techniti.install.before_install"
# after_install = "techniti.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "techniti.uninstall.before_uninstall"
# after_uninstall = "techniti.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "techniti.utils.before_app_install"
# after_app_install = "techniti.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "techniti.utils.before_app_uninstall"
# after_app_uninstall = "techniti.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "techniti.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Donation": {
		"on_submit": "techniti.api.update_stats_on_donation",
		"on_cancel": "techniti.api.update_stats_on_donation"
	}
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"techniti.tasks.all"
# 	],
# 	"daily": [
# 		"techniti.tasks.daily"
# 	],
# 	"hourly": [
# 		"techniti.tasks.hourly"
# 	],
# 	"weekly": [
# 		"techniti.tasks.weekly"
# 	],
# 	"monthly": [
# 		"techniti.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "techniti.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "techniti.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "techniti.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["techniti.utils.before_request"]
# after_request = ["techniti.utils.after_request"]

# Job Events
# ----------
# before_job = ["techniti.utils.before_job"]
# after_job = ["techniti.utils.after_job"]

# User Data Protection
# --------------------

user_data_fields = [
	{
		"doctype": "Donor",
		"filter_by": "email",
		"redact_fields": ["full_name", "mobile", "pan_number", "address"],
		"partial": 1,
	},
	{
		"doctype": "Donation",
		"filter_by": "donor_email",
		"redact_fields": ["donor_name", "donor_mobile", "message"],
		"partial": 1,
	}
]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"techniti.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Website Route Rules
website_route_rules = [
	{"from_route": "/donate", "to_route": "donate"},
	{"from_route": "/donation-success", "to_route": "donation-success"},
]

# Webhook endpoint for Razorpay
# The webhook URL will be: https://yoursite.com/api/method/techniti.api.razorpay_webhook
