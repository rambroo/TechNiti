# Copyright (c) 2024, TechNiti and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class TechnitiSettings(Document):
	pass


def get_razorpay_credentials():
	"""Get Razorpay credentials from settings"""
	settings = frappe.get_single("Techniti Settings")
	return {
		"key_id": settings.razorpay_key_id,
		"key_secret": settings.get_password("razorpay_key_secret"),
		"webhook_secret": settings.get_password("razorpay_webhook_secret") if settings.razorpay_webhook_secret else None
	}
