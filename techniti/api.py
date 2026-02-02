# Copyright (c) 2024, TechNiti and contributors
# For license information, please see license.txt

import hmac
import hashlib
import json
import frappe
import requests
from frappe import _


RAZORPAY_API_URL = "https://api.razorpay.com/v1"


def get_razorpay_credentials():
	"""Get Razorpay credentials from settings"""
	settings = frappe.get_single("Techniti Settings")
	if not settings.razorpay_key_id or not settings.razorpay_key_secret:
		frappe.throw(_("Razorpay credentials not configured. Please configure in Techniti Settings."))

	return {
		"key_id": settings.razorpay_key_id,
		"key_secret": settings.get_password("razorpay_key_secret"),
		"webhook_secret": settings.get_password("razorpay_webhook_secret") if settings.razorpay_webhook_secret else None
	}


@frappe.whitelist(allow_guest=True)
def create_donation_order(amount, campaign=None, full_name=None, email=None,
						  mobile=None, pan_number=None, message=None, is_anonymous=False):
	"""Create a donation order and Razorpay order"""
	try:
		amount = float(amount)
		if amount <= 0:
			frappe.throw(_("Amount must be greater than 0"))

		# Validate campaign minimum amount
		if campaign:
			campaign_doc = frappe.get_doc("Donation Campaign", campaign)
			if campaign_doc.minimum_amount and amount < campaign_doc.minimum_amount:
				frappe.throw(_("Minimum donation amount is {0}").format(campaign_doc.minimum_amount))

		credentials = get_razorpay_credentials()

		# Create or get donor
		donor = None
		if email:
			existing_donor = frappe.db.get_value("Donor", {"email": email}, "name")
			if existing_donor:
				donor = existing_donor
				# Update donor details
				donor_doc = frappe.get_doc("Donor", donor)
				if full_name and donor_doc.full_name != full_name:
					donor_doc.full_name = full_name
				if mobile and donor_doc.mobile != mobile:
					donor_doc.mobile = mobile
				if pan_number and donor_doc.pan_number != pan_number:
					donor_doc.pan_number = pan_number
				donor_doc.save(ignore_permissions=True)
			else:
				# Create new donor
				donor_doc = frappe.get_doc({
					"doctype": "Donor",
					"full_name": full_name,
					"email": email,
					"mobile": mobile,
					"pan_number": pan_number
				})
				donor_doc.insert(ignore_permissions=True)
				donor = donor_doc.name

		# Create Razorpay order
		order_data = {
			"amount": int(amount * 100),  # Amount in paisa
			"currency": "INR",
			"receipt": f"donation_{frappe.generate_hash(length=10)}",
			"notes": {
				"donor_name": full_name or "Anonymous",
				"donor_email": email or "",
				"campaign": campaign or ""
			}
		}

		response = requests.post(
			f"{RAZORPAY_API_URL}/orders",
			json=order_data,
			auth=(credentials["key_id"], credentials["key_secret"])
		)

		if response.status_code != 200:
			frappe.log_error(f"Razorpay order creation failed: {response.text}", "Donation Error")
			frappe.throw(_("Failed to create payment order. Please try again."))

		razorpay_order = response.json()

		# Create donation record
		donation = frappe.get_doc({
			"doctype": "Donation",
			"donor": donor,
			"donor_name": full_name,
			"donor_email": email,
			"donor_mobile": mobile,
			"campaign": campaign,
			"amount": amount,
			"message": message,
			"is_anonymous": is_anonymous,
			"razorpay_order_id": razorpay_order["id"],
			"payment_status": "Pending"
		})
		donation.insert(ignore_permissions=True)
		frappe.db.commit()

		return {
			"success": True,
			"donation_id": donation.name,
			"order_id": razorpay_order["id"],
			"amount": amount,
			"razorpay_key_id": credentials["key_id"]
		}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Donation Order Error")
		frappe.throw(str(e))


@frappe.whitelist(allow_guest=True)
def verify_donation_payment(donation_id, razorpay_payment_id, razorpay_order_id, razorpay_signature):
	"""Verify Razorpay payment signature and update donation status"""
	try:
		credentials = get_razorpay_credentials()

		# Verify signature
		message = f"{razorpay_order_id}|{razorpay_payment_id}"
		expected_signature = hmac.new(
			credentials["key_secret"].encode(),
			message.encode(),
			hashlib.sha256
		).hexdigest()

		if expected_signature != razorpay_signature:
			frappe.log_error(f"Invalid signature for donation {donation_id}", "Payment Verification Error")
			frappe.throw(_("Payment verification failed"))

		# Get payment details from Razorpay
		payment_response = requests.get(
			f"{RAZORPAY_API_URL}/payments/{razorpay_payment_id}",
			auth=(credentials["key_id"], credentials["key_secret"])
		)

		payment_method = None
		if payment_response.status_code == 200:
			payment_data = payment_response.json()
			payment_method = payment_data.get("method", "").upper()

		# Update donation
		donation = frappe.get_doc("Donation", donation_id)
		donation.on_payment_success(
			payment_id=razorpay_payment_id,
			order_id=razorpay_order_id,
			signature=razorpay_signature,
			payment_method=payment_method
		)

		# Send receipt email
		send_donation_receipt(donation.name)

		return {
			"success": True,
			"message": _("Payment verified successfully")
		}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Payment Verification Error")
		# Mark donation as failed
		try:
			donation = frappe.get_doc("Donation", donation_id)
			donation.on_payment_failure()
		except Exception:
			pass
		frappe.throw(str(e))


@frappe.whitelist(allow_guest=True)
def razorpay_webhook():
	"""Handle Razorpay webhook events"""
	try:
		payload = frappe.request.get_data(as_text=True)
		signature = frappe.request.headers.get("X-Razorpay-Signature")

		credentials = get_razorpay_credentials()

		# Verify webhook signature if secret is configured
		if credentials.get("webhook_secret") and signature:
			expected_signature = hmac.new(
				credentials["webhook_secret"].encode(),
				payload.encode(),
				hashlib.sha256
			).hexdigest()

			if expected_signature != signature:
				frappe.log_error("Invalid webhook signature", "Webhook Error")
				return {"status": "error", "message": "Invalid signature"}

		data = json.loads(payload)
		event = data.get("event")

		if event == "payment.captured":
			payment = data.get("payload", {}).get("payment", {}).get("entity", {})
			order_id = payment.get("order_id")

			# Find donation by order ID
			donation_name = frappe.db.get_value(
				"Donation",
				{"razorpay_order_id": order_id},
				"name"
			)

			if donation_name:
				donation = frappe.get_doc("Donation", donation_name)
				if donation.payment_status != "Paid":
					donation.razorpay_payment_id = payment.get("id")
					donation.payment_method = (payment.get("method") or "").upper()
					donation.payment_status = "Paid"
					donation.save(ignore_permissions=True)
					donation.submit()
					frappe.db.commit()

					# Send receipt
					send_donation_receipt(donation_name)

		elif event == "payment.failed":
			payment = data.get("payload", {}).get("payment", {}).get("entity", {})
			order_id = payment.get("order_id")

			donation_name = frappe.db.get_value(
				"Donation",
				{"razorpay_order_id": order_id},
				"name"
			)

			if donation_name:
				donation = frappe.get_doc("Donation", donation_name)
				if donation.payment_status == "Pending":
					donation.payment_status = "Failed"
					donation.save(ignore_permissions=True)
					frappe.db.commit()

		return {"status": "success"}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Webhook Error")
		return {"status": "error", "message": str(e)}


def send_donation_receipt(donation_name):
	"""Send donation receipt email"""
	try:
		settings = frappe.get_single("Techniti Settings")
		if not settings.send_donation_receipt:
			return

		donation = frappe.get_doc("Donation", donation_name)
		if not donation.donor_email:
			return

		subject = f"Thank you for your donation - {donation.name}"
		message = f"""
		<h2>Thank You for Your Donation!</h2>
		<p>Dear {donation.donor_name or 'Donor'},</p>
		<p>We have received your generous donation. Here are the details:</p>
		<table style="border-collapse: collapse; margin: 20px 0;">
			<tr>
				<td style="padding: 8px; border: 1px solid #ddd;"><strong>Donation ID</strong></td>
				<td style="padding: 8px; border: 1px solid #ddd;">{donation.name}</td>
			</tr>
			<tr>
				<td style="padding: 8px; border: 1px solid #ddd;"><strong>Amount</strong></td>
				<td style="padding: 8px; border: 1px solid #ddd;">₹{donation.amount:,.2f}</td>
			</tr>
			<tr>
				<td style="padding: 8px; border: 1px solid #ddd;"><strong>Payment ID</strong></td>
				<td style="padding: 8px; border: 1px solid #ddd;">{donation.razorpay_payment_id or 'N/A'}</td>
			</tr>
			<tr>
				<td style="padding: 8px; border: 1px solid #ddd;"><strong>Date</strong></td>
				<td style="padding: 8px; border: 1px solid #ddd;">{donation.donation_date}</td>
			</tr>
		</table>
		<p>Your support means a lot to us. Thank you for making a difference!</p>
		<p>Best regards,<br>TechNiti Team</p>
		"""

		frappe.sendmail(
			recipients=[donation.donor_email],
			subject=subject,
			message=message,
			now=True
		)
	except Exception as e:
		frappe.log_error(f"Failed to send donation receipt: {str(e)}", "Email Error")


@frappe.whitelist()
def get_donation_stats():
	"""Get donation statistics for dashboard"""
	stats = frappe.db.sql("""
		SELECT
			COUNT(*) as total_donations,
			COALESCE(SUM(amount), 0) as total_amount,
			COUNT(DISTINCT donor) as total_donors
		FROM `tabDonation`
		WHERE payment_status = 'Paid' AND docstatus = 1
	""", as_dict=True)[0]

	return stats


@frappe.whitelist()
def get_recent_donations(limit=10):
	"""Get recent donations"""
	donations = frappe.get_all(
		"Donation",
		filters={"payment_status": "Paid", "docstatus": 1},
		fields=["name", "donor_name", "amount", "campaign", "donation_date", "is_anonymous"],
		order_by="donation_date desc",
		limit=limit
	)

	for d in donations:
		if d.is_anonymous:
			d.donor_name = "Anonymous"

	return donations


def update_stats_on_donation(doc, method):
	"""Update donor and campaign stats when donation is submitted/cancelled"""
	if doc.donor:
		try:
			donor = frappe.get_doc("Donor", doc.donor)
			donor.update_donation_stats()
			donor.save(ignore_permissions=True)
		except Exception:
			pass

	if doc.campaign:
		try:
			campaign = frappe.get_doc("Donation Campaign", doc.campaign)
			campaign.update_collection_stats()
			campaign.save(ignore_permissions=True)
		except Exception:
			pass
