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
	settings = frappe.get_single("Website Donation Settings")
	if not settings.razorpay_key_id or not settings.razorpay_key_secret:
		frappe.throw(_("Razorpay credentials not configured. Please configure in Website Donation Settings."))

	return {
		"key_id": settings.razorpay_key_id,
		"key_secret": settings.get_password("razorpay_key_secret"),
		"webhook_secret": settings.get_password("razorpay_webhook_secret") if settings.razorpay_webhook_secret else None
	}


@frappe.whitelist()
def donor_logout():
	"""Log out the current donor session"""
	frappe.local.login_manager.logout()
	frappe.db.commit()
	return {"success": True}


@frappe.whitelist(allow_guest=True)
def get_causes():
	"""Return Cause records marked show_on_website, ordered by display_order"""
	return frappe.get_all(
		"Cause",
		filters={"show_on_website": 1},
		fields=["name", "cause", "display_order"],
		order_by="display_order asc, cause asc"
	)


@frappe.whitelist(allow_guest=True)
def create_donation_order(amount, campaign=None, cause=None, full_name=None, email=None,
						  mobile=None, id_type=None, id_number=None, message=None,
						  is_anonymous=False, is_club_donation=False):
	"""Create a donation order and Razorpay order"""
	try:
		amount = float(amount)
		if amount <= 0:
			frappe.throw(_("Amount must be greater than 0"))

		# Club Donation requires email
		is_club_donation = frappe.parse_json(is_club_donation) if isinstance(is_club_donation, str) else is_club_donation
		if is_club_donation and not email:
			frappe.throw(_("Email is required for Club Donation / Subscription"))

		# Validate campaign minimum amount
		if campaign:
			campaign_doc = frappe.get_doc("Website Donation Campaign", campaign)
			if campaign_doc.minimum_amount and amount < campaign_doc.minimum_amount:
				frappe.throw(_("Minimum donation amount is {0}").format(campaign_doc.minimum_amount))

		credentials = get_razorpay_credentials()

		# Create or get donor
		donor = None
		if full_name and id_type and id_number:
			# Try to find existing donor by id_type + id_number
			existing_donor = frappe.db.get_value(
				"Website Donor",
				{"id_type": id_type, "id_number": id_number},
				"name"
			)
			if existing_donor:
				donor = existing_donor
				# Update donor details
				donor_doc = frappe.get_doc("Website Donor", donor)
				if full_name and donor_doc.full_name != full_name:
					donor_doc.full_name = full_name
				if email and donor_doc.email != email:
					donor_doc.email = email
				if mobile and donor_doc.mobile != mobile:
					donor_doc.mobile = mobile
				donor_doc.save(ignore_permissions=True)
			else:
				# Create new donor
				donor_doc = frappe.get_doc({
					"doctype": "Website Donor",
					"full_name": full_name,
					"id_type": id_type,
					"id_number": id_number,
					"email": email,
					"mobile": mobile
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
			"doctype": "Website Donation",
			"donor": donor,
			"donor_name": full_name,
			"donor_email": email,
			"donor_mobile": mobile,
			"donor_id_type": id_type,
			"donor_id_number": id_number,
			"campaign": campaign,
			"cause": cause or None,
			"amount": amount,
			"message": message,
			"is_anonymous": is_anonymous,
			"is_club_donation": 1 if is_club_donation else 0,
			"razorpay_order_id": razorpay_order["id"],
			"payment_status": "Pending"
		})
		donation.insert(ignore_permissions=True)
		frappe.db.commit()

		return {
			"order_id": razorpay_order["id"],
			"amount": amount,
			"razorpay_key_id": credentials["key_id"],
			"donation_id": donation.name
		}

	except frappe.ValidationError:
		raise
	except Exception as e:
		frappe.log_error(f"create_donation_order failed: {str(e)}", "Donation Order Error")
		frappe.throw(_("Failed to create donation. Please try again."))


# ── Donor Portal Data ─────────────────────────────────────────────────────────

@frappe.whitelist()
def get_donor_portal_data():
	"""Return all data needed for the donor portal dashboard"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Please login to access the donor portal"), frappe.PermissionError)

	donor_name = frappe.db.get_value("Website Donor", {"linked_user": frappe.session.user}, "name")
	if not donor_name:
		frappe.throw(_("No donor account linked to this user."), frappe.PermissionError)

	donor = frappe.get_doc("Website Donor", donor_name)

	# Recent donations (last 20) — exclude Pending (pre-payment records created when Razorpay opens)
	donations = frappe.db.sql("""
		SELECT name, donation_date, amount, payment_status, cause, campaign,
			   is_club_donation, mode_of_payment, sent_receipt
		FROM `tabWebsite Donation`
		WHERE donor = %s AND payment_status IN ('Captured', 'Failed')
		ORDER BY donation_date DESC, creation DESC
		LIMIT 20
	""", donor_name, as_dict=True)

	# ALL subscriptions (active + expiring + expired) — donor sees full history
	all_subscriptions = frappe.db.sql("""
		SELECT name, cause, from_date, to_date, status, type,
			   cost, total_amount, number_of_months, start_month
		FROM `tabWebsite Donation Subscription`
		WHERE donor = %s
		ORDER BY from_date DESC
	""", donor_name, as_dict=True)

	# Attach donation_details breakdown to each subscription
	for sub in all_subscriptions:
		sub["details"] = frappe.db.sql("""
			SELECT plan, donation_date, end_date, cost
			FROM `tabWebsite Donation Sub Detail`
			WHERE parent = %s
			ORDER BY donation_date ASC
		""", sub["name"], as_dict=True)

	# Active/expiring subs (for summary stats)
	active_subscriptions = [s for s in all_subscriptions if s["status"] in ("On Processing", "Expiring Soon")]

	# Pending (expired subs with months owed)
	expired = frappe.db.sql("""
		SELECT name, cause, expired_date, total_amount, months_left,
			   donation_left_month, donor_subscription_type
		FROM `tabWebsite Expired Subscription`
		WHERE donor = %s
		ORDER BY expired_date DESC
	""", donor_name, as_dict=True)

	# Club details per cause
	clubs = []
	for row in donor.get("club_details", []):
		clubs.append({
			"cause": row.cause,
			"monthly_club_amount": row.monthly_club_amount,
			"status": row.status,
			"pending_months": row.pending_months,
			"pending_amount": row.pending_amount,
			"last_donation_date": str(row.last_donation_date) if row.last_donation_date else None
		})

	return {
		"donor": {
			"name": donor.name,
			"full_name": donor.full_name,
			"email": donor.email,
			"mobile": donor.mobile,
			"id_type": donor.id_type,
			"id_number": donor.id_number,
			"total_donated": donor.total_donated,
			"donation_count": donor.donation_count,
			"last_donation_date": str(donor.last_donation_date) if donor.last_donation_date else None,
			"subscription_status": donor.subscription_status,
			"is_club_donor": donor.is_club_donor,
			"donor_category": donor.donor_category
		},
		"donations": donations,
		"subscriptions": active_subscriptions,
		"all_subscriptions": all_subscriptions,
		"expired_subscriptions": expired,
		"clubs": clubs
	}


@frappe.whitelist(allow_guest=True)
def verify_donation_payment(donation_id, razorpay_payment_id, razorpay_order_id, razorpay_signature):
	"""Verify Razorpay payment and update donation status"""
	try:
		credentials = get_razorpay_credentials()

		# Step 1: Verify HMAC-SHA256 signature (proves request came from Razorpay)
		message = f"{razorpay_order_id}|{razorpay_payment_id}"
		expected_signature = hmac.new(
			credentials["key_secret"].encode(),
			message.encode(),
			hashlib.sha256
		).hexdigest()

		if not hmac.compare_digest(expected_signature, razorpay_signature):
			frappe.log_error(f"Signature mismatch for donation {donation_id}", "Payment Verification Error")
			frappe.throw(_("Payment verification failed — invalid signature"))

		# Step 2: Fetch actual payment status from Razorpay API
		payment_response = requests.get(
			f"{RAZORPAY_API_URL}/payments/{razorpay_payment_id}",
			auth=(credentials["key_id"], credentials["key_secret"])
		)

		if payment_response.status_code != 200:
			frappe.log_error(f"Razorpay API error: {payment_response.text}", "Payment Verification Error")
			frappe.throw(_("Could not fetch payment details from Razorpay"))

		payment_data = payment_response.json()
		rz_status = payment_data.get("status")  # captured / authorized / failed

		# Map Razorpay method → allowed mode_of_payment options
		_RZ_METHOD_MAP = {
			"upi": "UPI",
			"bank_transfer": "Bank Transfer",
			"netbanking": "Online",
			"card": "Online",
			"emi": "Online",
			"cardless_emi": "Online",
			"wallet": "Online",
			"paylater": "Online",
		}
		rz_method = payment_data.get("method", "")
		payment_method = _RZ_METHOD_MAP.get(rz_method, "Online")

		donation = frappe.get_doc("Website Donation", donation_id)

		# Avoid double-processing
		if donation.payment_status == "Captured":
			return {"success": True, "message": "Already captured"}

		if rz_status in ("captured", "authorized"):
			# authorized = bank deducted but not yet transferred; treat as success
			donation.on_payment_success(
				payment_id=razorpay_payment_id,
				order_id=razorpay_order_id,
				signature=razorpay_signature,
				payment_method=payment_method
			)
			# Enqueue receipt so the HTTP response returns fast
			frappe.enqueue(
				send_website_donation_receipt,
				queue="short",
				timeout=120,
				doc=frappe.get_doc("Website Donation", donation_id)
			)
			return {"success": True, "message": _("Payment successful")}

		else:
			# failed / anything else
			donation.on_payment_failure()
			return {"success": False, "message": _("Payment was not successful")}

	except frappe.ValidationError:
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Payment Verification Error")
		try:
			frappe.get_doc("Website Donation", donation_id).on_payment_failure()
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
			payment_id = payment.get("id")

			donation_name = frappe.db.get_value(
				"Website Donation", {"razorpay_order_id": order_id}, "name"
			)

			if donation_name:
				donation = frappe.get_doc("Website Donation", donation_name)
				# Idempotent — skip if already captured by the frontend callback
				if donation.payment_status != "Captured":
					_RZ_METHOD_MAP = {"upi": "UPI", "bank_transfer": "Bank Transfer"}
					donation.razorpay_payment_id = payment_id
					donation.mode_of_payment = _RZ_METHOD_MAP.get(payment.get("method", ""), "Online")
					donation.payment_status = "Captured"
					donation.save(ignore_permissions=True)
					if donation.docstatus == 0:
						donation.submit()
					frappe.db.commit()
					frappe.enqueue(
						send_website_donation_receipt,
						queue="short",
						timeout=120,
						doc=frappe.get_doc("Website Donation", donation_name)
					)

		elif event == "payment.failed":
			payment = data.get("payload", {}).get("payment", {}).get("entity", {})
			order_id = payment.get("order_id")

			donation_name = frappe.db.get_value(
				"Website Donation", {"razorpay_order_id": order_id}, "name"
			)

			if donation_name:
				donation = frappe.get_doc("Website Donation", donation_name)
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
		settings = frappe.get_single("Website Donation Settings")
		if not settings.send_donation_receipt:
			return

		donation = frappe.get_doc("Website Donation", donation_name)
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
		FROM `tabWebsite Donation`
		WHERE payment_status = 'Paid' AND docstatus = 1
	""", as_dict=True)[0]

	return stats


@frappe.whitelist()
def get_recent_donations(limit=10):
	"""Get recent donations"""
	donations = frappe.get_all(
		"Website Donation",
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
			donor = frappe.get_doc("Website Donor", doc.donor)
			donor.update_donation_stats()
			donor.save(ignore_permissions=True)
		except Exception:
			pass

	if doc.campaign:
		try:
			campaign = frappe.get_doc("Website Donation Campaign", doc.campaign)
			campaign.update_collection_stats()
			campaign.save(ignore_permissions=True)
		except Exception:
			pass


# ─────────────────────────────────────────────────────────────────────────────
# WEBSITE DONATION SYSTEM — NEW API FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist(allow_guest=True)
def get_donor_club_status(id_type, id_number):
	"""Check if a donor exists and return their club status per cause"""
	donor = frappe.db.get_value(
		"Website Donor",
		{"id_type": id_type, "id_number": id_number},
		["name", "full_name", "email", "mobile", "subscription_status", "is_club_donor"],
		as_dict=True
	)
	if not donor:
		return {"exists": False}

	donor_doc = frappe.get_doc("Website Donor", donor["name"])
	clubs = []
	for row in donor_doc.get("club_details", []):
		# Get the pending-from month from expired subscription record
		expired_rec = frappe.db.get_value(
			"Website Expired Subscription",
			{"donor": donor["name"], "cause": row.cause},
			["donation_left_month", "months_left"],
			as_dict=True
		)
		clubs.append({
			"cause": row.cause,
			"monthly_club_amount": row.monthly_club_amount,
			"status": row.status,
			"pending_months": row.pending_months,
			"pending_amount": row.pending_amount,
			"last_donation_date": str(row.last_donation_date) if row.last_donation_date else None,
			"pending_from_month": expired_rec.donation_left_month if expired_rec else None,
		})

	return {
		"exists": True,
		"donor_id": donor["name"],
		"full_name": donor["full_name"],
		"email": donor["email"],
		"mobile": donor["mobile"],
		"subscription_status": donor["subscription_status"],
		"is_club_donor": donor["is_club_donor"],
		"clubs": clubs
	}


@frappe.whitelist()
def get_logged_in_club_status():
	"""Return club status + last 3 club donations for the currently logged-in donor"""
	if frappe.session.user == "Guest":
		return {"exists": False}

	donor_name = frappe.db.get_value("Website Donor", {"linked_user": frappe.session.user}, "name")
	if not donor_name:
		return {"exists": False}

	donor = frappe.get_doc("Website Donor", donor_name)

	clubs = []
	for row in donor.get("club_details", []):
		expired_rec = frappe.db.get_value(
			"Website Expired Subscription",
			{"donor": donor_name, "cause": row.cause},
			["donation_left_month", "months_left"],
			as_dict=True
		)
		clubs.append({
			"cause": row.cause,
			"monthly_club_amount": row.monthly_club_amount,
			"status": row.status,
			"pending_months": row.pending_months,
			"pending_amount": row.pending_amount,
			"last_donation_date": str(row.last_donation_date) if row.last_donation_date else None,
			"pending_from_month": expired_rec.donation_left_month if expired_rec else None,
		})

	# Last 3 club donations for history display
	recent_donations = frappe.db.sql("""
		SELECT name, donation_date, amount, cause, number_of_months
		FROM `tabWebsite Donation`
		WHERE donor = %s AND is_club_donation = 1 AND payment_status = 'Captured'
		ORDER BY donation_date DESC, creation DESC
		LIMIT 3
	""", donor_name, as_dict=True)

	return {
		"exists": True,
		"donor_id": donor_name,
		"full_name": donor.full_name,
		"email": donor.email,
		"mobile": donor.mobile,
		"is_club_donor": donor.is_club_donor,
		"subscription_status": donor.subscription_status,
		"clubs": clubs,
		"recent_donations": [dict(d) for d in recent_donations]
	}


@frappe.whitelist(allow_guest=True)
def create_combined_club_order(months, full_name, id_type, id_number,
							   email=None, mobile=None, is_anonymous=0):
	"""Create a Razorpay order for a combined multi-cause club payment.
	months × sum(all active club monthly amounts) = total charge.
	Creates one pending Website Donation per cause involved.
	"""
	try:
		months = int(months)
		if months < 1:
			frappe.throw(_("Months must be at least 1"))

		donor_name_id = frappe.db.get_value(
			"Website Donor", {"id_type": id_type, "id_number": id_number}, "name")
		if not donor_name_id:
			frappe.throw(_("No club account found. Please complete a first-time donation first."))

		donor_doc = frappe.get_doc("Website Donor", donor_name_id)
		active_clubs = [
			row for row in donor_doc.get("club_details", [])
			if row.status == "Active" and row.monthly_club_amount
		]
		if not active_clubs:
			frappe.throw(_("No active club memberships found for this donor."))

		# Optionally allow amount override per settings
		settings = frappe.get_single("Website Donation Settings")
		allow_change = settings.get("allow_donor_amount_change")

		total_amount = sum(float(row.monthly_club_amount) for row in active_clubs) * months
		total_amount = round(total_amount, 2)
		if total_amount <= 0:
			frappe.throw(_("Total amount must be greater than 0"))

		# Update donor details if changed
		if full_name and donor_doc.full_name != full_name:
			donor_doc.full_name = full_name
		if email and donor_doc.email != email:
			donor_doc.email = email
		if mobile and donor_doc.mobile != mobile:
			donor_doc.mobile = mobile
		donor_doc.save(ignore_permissions=True)

		credentials = get_razorpay_credentials()

		order_data = {
			"amount": int(total_amount * 100),
			"currency": "INR",
			"receipt": f"club_{frappe.generate_hash(length=8)}",
			"notes": {
				"donor_name": full_name or donor_doc.full_name,
				"donor_email": email or donor_doc.email or "",
				"months": str(months),
				"type": "combined_club"
			}
		}
		response = requests.post(
			f"{RAZORPAY_API_URL}/orders",
			json=order_data,
			auth=(credentials["key_id"], credentials["key_secret"])
		)
		if response.status_code != 200:
			frappe.log_error(f"Razorpay order failed: {response.text}", "Combined Club Order Error")
			frappe.throw(_("Failed to create payment order. Please try again."))

		razorpay_order = response.json()

		# Create one pending Website Donation per cause
		donation_ids = []
		for club_row in active_clubs:
			cause_amount = round(float(club_row.monthly_club_amount) * months, 2)
			don = frappe.get_doc({
				"doctype": "Website Donation",
				"donor": donor_name_id,
				"cause": club_row.cause,
				"amount": cause_amount,
				"is_club_donation": 1,
				"is_anonymous": is_anonymous,
				"mode_of_payment": "Online",
				"payment_status": "Pending",
				"razorpay_order_id": razorpay_order["id"],
				"receipt_donor_check": 1,
				"number_of_months": months
			})
			don.insert(ignore_permissions=True)
			donation_ids.append({"donation_id": don.name, "cause": club_row.cause, "amount": cause_amount})

		frappe.db.commit()

		return {
			"success": True,
			"order_id": razorpay_order["id"],
			"total_amount": total_amount,
			"months": months,
			"donations": donation_ids,
			"razorpay_key_id": credentials["key_id"],
			"donor_id": donor_name_id,
			"donor_name": donor_doc.full_name,
			"email": email or donor_doc.email or "",
			"mobile": mobile or donor_doc.mobile or ""
		}

	except frappe.ValidationError:
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Combined Club Order Error")
		frappe.throw(str(e))


@frappe.whitelist(allow_guest=True)
def handle_combined_club_callback(order_id, razorpay_payment_id,
								  razorpay_order_id, razorpay_signature):
	"""Verify combined club payment and create subscriptions per cause."""
	try:
		credentials = get_razorpay_credentials()

		msg = f"{razorpay_order_id}|{razorpay_payment_id}"
		expected_sig = hmac.new(
			credentials["key_secret"].encode(), msg.encode(), hashlib.sha256
		).hexdigest()
		if expected_sig != razorpay_signature:
			frappe.throw(_("Payment verification failed"))

		# Get all pending donations for this order
		pending_donations = frappe.get_all(
			"Website Donation",
			filters={"razorpay_order_id": razorpay_order_id, "payment_status": "Pending"},
			pluck="name"
		)
		if not pending_donations:
			return {"success": True, "message": "Already processed"}

		# Fetch payment method from Razorpay
		payment_response = requests.get(
			f"{RAZORPAY_API_URL}/payments/{razorpay_payment_id}",
			auth=(credentials["key_id"], credentials["key_secret"])
		)
		rz_method = ""
		if payment_response.status_code == 200:
			rz_method = payment_response.json().get("method", "")
		_RZ_METHOD_MAP = {"upi": "UPI", "bank_transfer": "Bank Transfer"}
		payment_method = _RZ_METHOD_MAP.get(rz_method, "Online")

		first_donation_id = None
		for donation_name in pending_donations:
			donation = frappe.get_doc("Website Donation", donation_name)
			donation.razorpay_payment_id = razorpay_payment_id
			donation.razorpay_signature = razorpay_signature
			donation.payment_status = "Captured"
			donation.mode_of_payment = payment_method
			donation.save(ignore_permissions=True)

			# Create subscription for this cause
			if not frappe.db.exists("Website Donation Subscription",
									{"donor": donation.donor, "razorpay_order_id": razorpay_order_id,
									 "cause": donation.cause}):
				settings = frappe.get_single("Website Donation Settings")
				cause = donation.cause or settings.default_cause or None
				_create_subscription_from_donation(donation, cause)

			frappe.enqueue(
				send_website_donation_receipt, queue="short", timeout=120,
				doc=frappe.get_doc("Website Donation", donation_name)
			)
			if not first_donation_id:
				first_donation_id = donation_name

		frappe.db.commit()
		return {"success": True, "redirect_id": first_donation_id}

	except frappe.ValidationError:
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Combined Club Callback Error")
		try:
			frappe.db.sql(
				"UPDATE `tabWebsite Donation` SET payment_status='Failed' WHERE razorpay_order_id=%s AND payment_status='Pending'",
				razorpay_order_id
			)
			frappe.db.commit()
		except Exception:
			pass
		frappe.throw(str(e))


@frappe.whitelist()
def update_donor_club(donor_name, club_changes):
	"""Backend-only: update a donor's club cause/amount (Netflix-style upgrade/downgrade).
	club_changes: list of {cause, monthly_amount, action} where action = 'update'|'remove'|'add'
	"""
	if frappe.session.user == "Guest":
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	if not frappe.has_permission("Website Donor", "write"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	if isinstance(club_changes, str):
		club_changes = frappe.parse_json(club_changes)

	donor_doc = frappe.get_doc("Website Donor", donor_name)
	changed = []

	for change in club_changes:
		action = change.get("action", "update")
		cause = change.get("cause")
		new_amount = float(change.get("monthly_amount", 0)) if change.get("monthly_amount") else 0

		existing_row = next((r for r in donor_doc.get("club_details", []) if r.cause == cause), None)

		if action == "remove" and existing_row:
			donor_doc.club_details.remove(existing_row)
			changed.append(f"Removed {cause} club")

		elif action == "add" and not existing_row:
			donor_doc.append("club_details", {
				"cause": cause,
				"monthly_club_amount": new_amount,
				"status": "Active"
			})
			changed.append(f"Added {cause} club at ₹{new_amount}/month")

		elif action == "update" and existing_row:
			old_amount = float(existing_row.monthly_club_amount or 0)
			existing_row.monthly_club_amount = new_amount
			existing_row.status = "Active"
			changed.append(f"Updated {cause}: ₹{old_amount} → ₹{new_amount}/month")

	if changed:
		donor_doc.save(ignore_permissions=True)
		frappe.db.commit()

	return {"success": True, "changes": changed}


@frappe.whitelist(allow_guest=True)
def get_donation_settings_public():
	"""Return public-facing settings for the donate page"""
	settings = frappe.get_single("Website Donation Settings")
	return {
		"allow_donor_amount_change": settings.get("allow_donor_amount_change") or 0
	}


@frappe.whitelist(allow_guest=True)
def create_website_donation_order(full_name, id_type, id_number, amount,
								   email=None, mobile=None, campaign=None, cause=None,
								   message=None, is_anonymous=0, is_club_donation=0,
								   is_company_donation=0, company_name=None, sub_donor=None,
								   months=1):
	"""Create donation order — handles both one-time and club donations"""
	try:
		amount = float(amount)
		if amount <= 0:
			frappe.throw(_("Amount must be greater than 0"))

		is_club_donation = frappe.parse_json(is_club_donation) if isinstance(is_club_donation, str) else is_club_donation
		is_company_donation = frappe.parse_json(is_company_donation) if isinstance(is_company_donation, str) else is_company_donation

		if is_club_donation and not email:
			frappe.throw(_("Email is required for Club Donation / Subscription"))

		credentials = get_razorpay_credentials()

		# Find or create donor
		donor_name_id = frappe.db.get_value(
			"Website Donor", {"id_type": id_type, "id_number": id_number}, "name")

		if donor_name_id:
			donor_doc = frappe.get_doc("Website Donor", donor_name_id)
			if full_name and donor_doc.full_name != full_name.upper():
				donor_doc.full_name = full_name
			if email and donor_doc.email != email:
				donor_doc.email = email
			if mobile and donor_doc.mobile != mobile:
				donor_doc.mobile = mobile
			donor_doc.save(ignore_permissions=True)
		else:
			donor_doc = frappe.get_doc({
				"doctype": "Website Donor",
				"full_name": full_name,
				"id_type": id_type,
				"id_number": id_number,
				"email": email or None,
				"mobile": mobile or None
			})
			donor_doc.insert(ignore_permissions=True)
		donor_name_id = donor_doc.name

		# Create Razorpay order
		order_data = {
			"amount": int(amount * 100),
			"currency": "INR",
			"receipt": f"wdon_{frappe.generate_hash(length=8)}",
			"notes": {
				"donor_name": full_name or "Anonymous",
				"donor_email": email or "",
				"cause": cause or "",
				"campaign": campaign or ""
			}
		}

		response = requests.post(
			f"{RAZORPAY_API_URL}/orders",
			json=order_data,
			auth=(credentials["key_id"], credentials["key_secret"])
		)

		if response.status_code != 200:
			frappe.log_error(f"Razorpay order failed: {response.text}", "Website Donation Error")
			frappe.throw(_("Failed to create payment order. Please try again."))

		razorpay_order = response.json()

		# Create Website Donation record
		_months = int(months) if is_club_donation and months else 1
		donation = frappe.get_doc({
			"doctype": "Website Donation",
			"donor": donor_name_id,
			"cause": cause or None,
			"campaign": campaign or None,
			"amount": amount,
			"message": message,
			"is_anonymous": is_anonymous,
			"is_club_donation": 1 if is_club_donation else 0,
			"number_of_months": _months if is_club_donation else 0,
			"is_company_donation": 1 if is_company_donation else 0,
			"company_name": company_name if is_company_donation else None,
			"sub_donor": sub_donor if is_company_donation else None,
			"mode_of_payment": "Online",
			"payment_status": "Pending",
			"razorpay_order_id": razorpay_order["id"],
			"receipt_donor_check": 1
		})
		donation.insert(ignore_permissions=True)
		frappe.db.commit()

		return {
			"success": True,
			"donation_id": donation.name,
			"donor_id": donor_name_id,
			"order_id": razorpay_order["id"],
			"amount": amount,
			"razorpay_key_id": credentials["key_id"]
		}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Website Donation Order Error")
		frappe.throw(str(e))


@frappe.whitelist(allow_guest=True)
def handle_website_donation_callback(donation_id, razorpay_payment_id,
									  razorpay_order_id, razorpay_signature):
	"""Verify payment and finalise the donation"""
	try:
		credentials = get_razorpay_credentials()

		msg = f"{razorpay_order_id}|{razorpay_payment_id}"
		expected_sig = hmac.new(
			credentials["key_secret"].encode(),
			msg.encode(),
			hashlib.sha256
		).hexdigest()

		if expected_sig != razorpay_signature:
			frappe.throw(_("Payment verification failed"))

		donation = frappe.get_doc("Website Donation", donation_id)
		donation.razorpay_payment_id = razorpay_payment_id
		donation.razorpay_signature = razorpay_signature
		donation.payment_status = "Captured"
		donation.save(ignore_permissions=True)

		# If club donation, create a Website Donation Subscription (12 months)
		if donation.is_club_donation and not frappe.db.exists(
			"Website Donation Subscription", {"donor": donation.donor, "razorpay_order_id": razorpay_order_id}
		):
			settings = frappe.get_single("Website Donation Settings")
			cause = donation.cause or (settings.default_cause if settings.default_cause else None)
			_create_subscription_from_donation(donation, cause)

		# Send receipt email
		send_website_donation_receipt(donation)

		frappe.db.commit()
		return {"success": True}

	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Donation Callback Error")
		try:
			frappe.db.set_value("Website Donation", donation_id, "payment_status", "Failed")
			frappe.db.commit()
		except Exception:
			pass
		frappe.throw(str(e))


def _create_subscription_from_donation(donation, cause):
	"""Create a subscription from a club donation.

	donation.amount  = total paid for this cause (monthly_cost × months)
	donation.number_of_months = how many months were chosen (defaults to 12)
	"""
	from frappe.utils import get_first_day, today
	months = int(donation.number_of_months or 12)
	monthly_cost = round(float(donation.amount) / months, 2)
	from_date = get_first_day(donation.donation_date or today())
	sub = frappe.get_doc({
		"doctype": "Website Donation Subscription",
		"donor": donation.donor,
		"from_date": from_date,
		"start_month": from_date,
		"type": "Fixed",
		"total_amount": donation.amount,
		"number_of_months": months,
		"cost": monthly_cost,
		"cause": cause,
		"mode_of_payment": donation.mode_of_payment or "Online",
		"payment_status": "Captured",
		"razorpay_order_id": donation.razorpay_order_id,
		"razorpay_payment_id": donation.razorpay_payment_id,
		"receipt_donor_check": donation.receipt_donor_check,
		"receipt_donor": donation.receipt_donor or donation.donor,
		"is_company_donation": donation.is_company_donation,
		"company_name": donation.company_name,
		"sub_donor": donation.sub_donor
	})
	sub.insert(ignore_permissions=True)
	frappe.db.set_value("Website Donation", donation.name, "subscription", sub.name, update_modified=False)


def send_website_donation_receipt(doc, method=None):
	"""Send thank-you email with receipt after donation"""
	try:
		if isinstance(doc, str):
			doc = frappe.get_doc("Website Donation", doc)

		# Only send receipt for successfully captured payments
		if doc.payment_status != "Captured":
			return

		settings = frappe.get_single("Website Donation Settings")
		if not settings.send_donation_receipt:
			return

		email_to = doc.receipt_donor_email or doc.donor_email
		if not email_to:
			return

		subject = settings.thank_you_email_subject or f"Thank you for your donation - {doc.name}"
		donor_display = doc.receipt_donor_name or doc.donor_name or "Donor"
		body = settings.thank_you_email_body or f"""
		<h2>Thank You for Your Donation!</h2>
		<p>Dear {donor_display},</p>
		<p>We have received your generous donation. Here are the details:</p>
		<table style="border-collapse:collapse;margin:20px 0;">
			<tr><td style="padding:8px;border:1px solid #ddd;"><strong>Donation ID</strong></td><td style="padding:8px;border:1px solid #ddd;">{doc.name}</td></tr>
			<tr><td style="padding:8px;border:1px solid #ddd;"><strong>Amount</strong></td><td style="padding:8px;border:1px solid #ddd;">₹{doc.amount:,.2f}</td></tr>
			<tr><td style="padding:8px;border:1px solid #ddd;"><strong>ID Number</strong></td><td style="padding:8px;border:1px solid #ddd;">{doc.id_number_receipt_donor or doc.donor_id_number or 'N/A'}</td></tr>
			<tr><td style="padding:8px;border:1px solid #ddd;"><strong>Payment ID</strong></td><td style="padding:8px;border:1px solid #ddd;">{doc.razorpay_payment_id or 'N/A'}</td></tr>
			<tr><td style="padding:8px;border:1px solid #ddd;"><strong>Date</strong></td><td style="padding:8px;border:1px solid #ddd;">{doc.donation_date}</td></tr>
		</table>
		<p>Thank you for making a difference!</p>
		"""

		recipients = [email_to]
		if settings.cc_emails:
			recipients += [e.strip() for e in settings.cc_emails.split(",") if e.strip()]

		frappe.sendmail(recipients=recipients, subject=subject, message=body, now=True)
		frappe.db.set_value("Website Donation", doc.name, {
			"sent_receipt": 1,
			"sent_receipt_date": frappe.utils.today()
		}, update_modified=False)

	except Exception as e:
		frappe.log_error(f"Failed to send donation receipt: {str(e)}", "Receipt Email Error")


@frappe.whitelist(allow_guest=True)
def get_website_donation_stats():
	"""Return aggregate stats for the public donate page"""
	stats = frappe.db.sql("""
		SELECT COUNT(*) as total_donations,
			   COALESCE(SUM(amount), 0) as total_amount,
			   COUNT(DISTINCT donor) as total_donors
		FROM `tabWebsite Donation`
		WHERE payment_status = 'Captured'
	""", as_dict=True)[0]

	active_campaigns = frappe.db.count("Website Donation Campaign", {"status": "Active"})
	stats["active_campaigns"] = active_campaigns
	return stats


# ── OTP Authentication ──────────────────────────────────────────────────────

@frappe.whitelist(allow_guest=True)
def request_donor_otp(identifier):
	"""Send a 6-digit OTP to the donor's email (or mobile in future)"""
	import random
	identifier = (identifier or "").strip()
	if not identifier:
		frappe.throw(_("Please enter your email or phone number"))

	# Search by email first, then by mobile
	donor_name = frappe.db.get_value("Website Donor", {"email": identifier}, "name")
	if not donor_name:
		donor_name = frappe.db.get_value("Website Donor", {"mobile": identifier}, "name")

	if not donor_name:
		return {"success": False, "message": "No donor found with this email or phone. Please register via the donation page."}

	donor = frappe.db.get_value(
		"Website Donor", donor_name,
		["name", "email", "mobile", "full_name", "linked_user"],
		as_dict=True
	)

	if not donor:
		return {"success": False, "message": "No donor found with this email or phone. Please register via the donation page."}

	otp = str(random.randint(100000, 999999))
	cache_key = f"donor_otp_{identifier}"
	frappe.cache().set_value(cache_key, {"otp": otp, "donor": donor["name"]}, expires_in_sec=300)

	# Enqueue email so HTTP response returns immediately (worker sends it async)
	if donor.get("email"):
		frappe.enqueue(
			_send_otp_email,
			queue="short",
			timeout=60,
			email=donor["email"],
			full_name=donor.get("full_name", "Donor"),
			otp=otp
		)

	return {"success": True, "message": "OTP sent successfully"}


def _send_otp_email(email, full_name, otp):
	"""Background task: send OTP email"""
	frappe.sendmail(
		recipients=[email],
		subject="Your OTP for Donor Portal",
		message=f"""
		<div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;padding:32px;background:#f9f9f9;border-radius:12px;">
			<h2 style="color:#25283a;margin-bottom:8px;">Donor Portal Login</h2>
			<p style="color:#555;">Dear {full_name},</p>
			<p style="color:#555;">Your one-time password (OTP) to access the Donor Portal is:</p>
			<div style="text-align:center;margin:24px 0;">
				<span style="font-size:40px;font-weight:bold;letter-spacing:10px;color:#3cc88f;background:#f0fff8;padding:12px 24px;border-radius:8px;display:inline-block;">{otp}</span>
			</div>
			<p style="color:#888;font-size:13px;">This OTP is valid for <strong>5 minutes</strong>. Do not share it with anyone.</p>
		</div>
		""",
		now=True
	)


@frappe.whitelist(allow_guest=True)
def verify_donor_otp(identifier, otp):
	"""Verify OTP and log in the donor"""
	identifier = (identifier or "").strip()
	cache_key = f"donor_otp_{identifier}"
	cached = frappe.cache().get_value(cache_key)

	if not cached:
		return {"success": False, "message": "OTP expired or not found. Please request a new one."}

	if str(cached.get("otp")) != str(otp):
		return {"success": False, "message": "Invalid OTP. Please try again."}

	frappe.cache().delete_value(cache_key)

	donor_name = cached.get("donor")
	donor = frappe.get_doc("Website Donor", donor_name)

	# Ensure linked user exists — create one if missing
	if not donor.linked_user:
		if not donor.email:
			return {"success": False, "message": "No email on donor account. Please contact support."}

		# Ensure role exists
		if not frappe.db.exists("Role", "Website Donor"):
			frappe.get_doc({"doctype": "Role", "role_name": "Website Donor", "desk_access": 0}).insert(ignore_permissions=True)

		if frappe.db.exists("User", donor.email):
			linked = donor.email
		else:
			new_user = frappe.get_doc({
				"doctype": "User",
				"email": donor.email,
				"first_name": donor.full_name.title() if donor.full_name else "",
				"user_type": "Website User",
				"send_welcome_email": 0,
				"roles": [{"role": "Website Donor"}]
			})
			new_user.insert(ignore_permissions=True)
			linked = new_user.name

		frappe.db.set_value("Website Donor", donor.name, "linked_user", linked, update_modified=False)
		frappe.db.commit()
		donor.linked_user = linked

	try:
		frappe.local.login_manager.login_as(donor.linked_user)
		return {"success": True, "redirect": "/donor-portal"}
	except Exception as e:
		frappe.log_error(str(e), "OTP Login Error")
		return {"success": False, "message": "Login failed. Please contact support."}


@frappe.whitelist(allow_guest=True)
def request_donor_whatsapp_otp(mobile):
	"""Send a 6-digit OTP via WhatsApp using the 'login' template (expires in 10 minutes)."""
	import random
	from techniti.whatsapp.whatsapp import SparklebotHandler

	digits = "".join(filter(str.isdigit, mobile or ""))
	if len(digits) < 7:
		return {"success": False, "message": "Please enter a valid mobile number"}

	# Match last 10 digits to handle numbers stored with or without country code
	local = digits[-10:] if len(digits) > 10 else digits
	donors = frappe.get_all(
		"Website Donor",
		filters=[["mobile", "like", f"%{local}"]],
		fields=["name", "full_name", "mobile", "email"],
		limit=1,
	)
	if not donors:
		return {"success": False, "message": "No donor account found with this mobile number"}

	handler = SparklebotHandler()
	if not handler.is_enabled():
		return {"success": False, "message": "WhatsApp service is not enabled"}

	otp = str(random.randint(100000, 999999))
	cache_key = f"donor_otp_wa_{digits}"
	frappe.cache().set_value(cache_key, {"otp": otp, "donor": donors[0].name}, expires_in_sec=600)

	handler.send_template(
		phone=digits,
		template_name="login",
		language="en",
		field_params={"field_1": otp},
		doctype="Website Donor",
		docname=donors[0].name,
	)

	masked = digits[:2] + "X" * (len(digits) - 4) + digits[-2:]
	return {"success": True, "message": f"OTP sent to WhatsApp number ending {digits[-4:]}", "masked": masked}


@frappe.whitelist(allow_guest=True)
def verify_donor_whatsapp_otp(mobile, otp):
	"""Verify WhatsApp OTP and log the donor in."""
	digits = "".join(filter(str.isdigit, mobile or ""))
	cache_key = f"donor_otp_wa_{digits}"
	cached = frappe.cache().get_value(cache_key)

	if not cached:
		return {"success": False, "message": "OTP expired or not found. Please request a new one."}
	if str(cached.get("otp")).strip() != str(otp).strip():
		return {"success": False, "message": "Invalid OTP. Please try again."}

	frappe.cache().delete_value(cache_key)

	donor_name = cached.get("donor")
	donor = frappe.get_doc("Website Donor", donor_name)

	if not donor.linked_user:
		if not donor.email:
			return {"success": False, "message": "No email on donor account. Please contact support."}

		if not frappe.db.exists("Role", "Website Donor"):
			frappe.get_doc({"doctype": "Role", "role_name": "Website Donor", "desk_access": 0}).insert(ignore_permissions=True)

		if frappe.db.exists("User", donor.email):
			linked = donor.email
		else:
			new_user = frappe.get_doc({
				"doctype": "User",
				"email": donor.email,
				"first_name": donor.full_name.title() if donor.full_name else "",
				"user_type": "Website User",
				"send_welcome_email": 0,
				"roles": [{"role": "Website Donor"}]
			})
			new_user.insert(ignore_permissions=True)
			linked = new_user.name

		frappe.db.set_value("Website Donor", donor.name, "linked_user", linked, update_modified=False)
		frappe.db.commit()
		donor.linked_user = linked

	try:
		frappe.local.login_manager.login_as(donor.linked_user)
		return {"success": True, "redirect": "/donor-portal"}
	except Exception as e:
		frappe.log_error(title="WhatsApp OTP Login Error", message=str(e))
		return {"success": False, "message": "Login failed. Please contact support."}


@frappe.whitelist(allow_guest=True)
def register_donor(full_name, email=None, mobile=None, reg_channel="email", id_type=None, id_number=None):
	"""Register a new donor account. reg_channel = 'email' or 'whatsapp'."""
	import random
	full_name = (full_name or "").strip()
	email = (email or "").strip().lower()
	mobile = (mobile or "").strip()

	if not full_name:
		return {"success": False, "message": "Please enter your full name."}

	if reg_channel == "whatsapp":
		if not mobile:
			return {"success": False, "message": "Please enter your mobile number."}
		digits = "".join(filter(str.isdigit, mobile))
		if len(digits) < 7:
			return {"success": False, "message": "Please enter a valid mobile number."}
		local = digits[-10:] if len(digits) > 10 else digits
		existing = frappe.get_all("Website Donor", filters=[["mobile", "like", f"%{local}"]], fields=["name"], limit=1)
		if existing:
			return {"success": False, "exists": True, "message": "An account with this mobile number already exists. Please login instead."}
		# Frappe User requires an email — generate a non-public placeholder
		if not email:
			email = f"donor_{digits}@noreply.wcww.in"
	else:
		if not email:
			return {"success": False, "message": "Please enter your email address."}
		existing = frappe.db.get_value("Website Donor", {"email": email}, "name")
		if existing:
			return {"success": False, "exists": True, "message": "An account with this email already exists. Please login instead."}

	doc_data = {
		"doctype": "Website Donor",
		"naming_series": "WDONOR-.#####",
		"full_name": full_name,
		"email": email,
		"mobile": mobile or "",
	}
	if id_type:
		doc_data["id_type"] = id_type
	if id_number:
		doc_data["id_number"] = id_number
	donor = frappe.get_doc(doc_data)
	donor.insert(ignore_permissions=True)
	frappe.db.commit()

	otp = str(random.randint(100000, 999999))

	if reg_channel == "whatsapp":
		from techniti.whatsapp.whatsapp import SparklebotHandler
		cache_key = f"donor_otp_wa_{digits}"
		frappe.cache().set_value(cache_key, {"otp": otp, "donor": donor.name}, expires_in_sec=600)
		handler = SparklebotHandler()
		if handler.is_enabled():
			handler.send_template(
				phone=digits,
				template_name="login",
				language="en",
				field_params={"field_1": otp},
				doctype="Website Donor",
				docname=donor.name,
			)
		masked = digits[:2] + "X" * (len(digits) - 4) + digits[-2:]
		return {"success": True, "channel": "whatsapp", "identifier": digits, "masked": masked,
				"message": "Account created! Please verify with the OTP sent to your WhatsApp."}
	else:
		cache_key = f"donor_otp_{email}"
		frappe.cache().set_value(cache_key, {"otp": otp, "donor": donor.name}, expires_in_sec=300)
		frappe.enqueue(
			_send_otp_email,
			queue="short",
			timeout=60,
			email=email,
			full_name=full_name,
			otp=otp
		)
		return {"success": True, "channel": "email", "identifier": email,
				"message": "Account created! Please verify your email with the OTP we sent."}


# ── Scheduler Tasks ──────────────────────────────────────────────────────────

def check_website_subscription_status():
	"""Daily: Update subscription status based on to_date"""
	from frappe.utils import getdate, today, date_diff
	subscriptions = frappe.get_all("Website Donation Subscription",
		fields=["name", "to_date", "status"])
	today_date = getdate(today())

	for sub in subscriptions:
		if not sub.to_date:
			continue
		to_date = getdate(sub.to_date)
		days_left = date_diff(to_date, today_date)
		if days_left < 0:
			new_status = "Expired"
		elif days_left <= 30:
			new_status = "Expiring Soon"
		else:
			new_status = "On Processing"

		if sub.status != new_status:
			frappe.db.set_value("Website Donation Subscription", sub.name, "status", new_status, update_modified=False)

	frappe.db.commit()


def update_website_donor_categories():
	"""Daily: Update donor_category based on total donations in last 365 days"""
	from frappe.utils import add_days, today
	cutoff = add_days(today(), -365)

	totals = frappe.db.sql("""
		SELECT donor, COALESCE(SUM(amount), 0) as total
		FROM `tabWebsite Donation`
		WHERE payment_status = 'Captured' AND donation_date >= %s
		GROUP BY donor
	""", cutoff, as_dict=True)

	total_map = {row.donor: row.total for row in totals}

	all_donors = frappe.get_all("Website Donor", pluck="name")
	for dn in all_donors:
		total = total_map.get(dn, 0)
		if total == 0:
			cat = "Inactive"
		elif total < 5000:
			cat = "Bronze"
		elif total < 10000:
			cat = "Silver"
		elif total < 50000:
			cat = "Gold"
		elif total < 100000:
			cat = "Diamond"
		else:
			cat = "Platinum"
		frappe.db.set_value("Website Donor", dn, "donor_category", cat, update_modified=False)

	frappe.db.commit()


def update_website_donor_status():
	"""Daily: Update subscription_status and is_club_donor on Website Donor"""
	all_donors = frappe.get_all("Website Donor", fields=["name", "subscription_status"])
	for donor in all_donors:
		active_sub = frappe.db.exists("Website Donation Subscription",
			{"donor": donor.name, "status": ["in", ["On Processing", "Expiring Soon"]]})
		if active_sub:
			if donor.subscription_status not in ("Active", "Paused"):
				frappe.db.set_value("Website Donor", donor.name, {
					"subscription_status": "Active",
					"is_club_donor": 1
				}, update_modified=False)
		else:
			expired_sub = frappe.db.exists("Website Donation Subscription",
				{"donor": donor.name, "status": "Expired"})
			if expired_sub and donor.subscription_status not in ("Paused",):
				frappe.db.set_value("Website Donor", donor.name, "subscription_status", "Expired", update_modified=False)

	frappe.db.commit()


@frappe.whitelist(allow_guest=True, methods=["POST", "GET"])
def update_website_expired_subscriptions(doc=None, method=None):
	"""Create/update Website Expired Subscription records for lapsed subscriptions"""
	from frappe.utils import getdate, today, get_first_day, date_diff, add_to_date, formatdate

	today_date = getdate(today())

	# Get latest subscription per donor+cause
	latest_subs = frappe.db.sql("""
		SELECT donor, cause, MAX(to_date) as max_to_date
		FROM `tabWebsite Donation Subscription`
		GROUP BY donor, cause
	""", as_dict=True)

	processed = set()

	for row in latest_subs:
		if not row.cause or not row.donor:
			continue

		sub = frappe.db.sql("""
			SELECT name, to_date, type, cost, start_month
			FROM `tabWebsite Donation Subscription`
			WHERE donor = %s AND cause = %s AND to_date = %s
			ORDER BY modified DESC LIMIT 1
		""", (row.donor, row.cause, row.max_to_date), as_dict=True)

		if not sub:
			continue
		sub = sub[0]

		donor_doc = frappe.get_doc("Website Donor", row.donor)

		# Skip paused donors
		if donor_doc.subscription_status == "Paused":
			pause_date = getdate(donor_doc.pause_date) if donor_doc.pause_date else None
			if pause_date and today_date <= pause_date:
				# Clean up any existing expired record
				existing = frappe.db.get_value("Website Expired Subscription",
					{"donor": row.donor, "cause": row.cause}, "name")
				if existing:
					frappe.delete_doc("Website Expired Subscription", existing, ignore_permissions=True)
				continue

		to_date = getdate(sub.to_date)
		if to_date >= today_date:
			# Subscription still active — remove expired record if exists
			existing = frappe.db.get_value("Website Expired Subscription",
				{"donor": row.donor, "cause": row.cause}, "name")
			if existing:
				frappe.delete_doc("Website Expired Subscription", existing, ignore_permissions=True)
			continue

		# Calculate months pending
		months_left = 0
		pending_start = add_to_date(to_date, days=1)
		temp = getdate(pending_start)
		while temp <= today_date:
			months_left += 1
			temp = add_to_date(temp, months=1)

		monthly_cost = sub.cost or 0
		total_pending = months_left * float(monthly_cost)
		pending_from = formatdate(get_first_day(pending_start), "MMM YYYY")

		existing_name = frappe.db.get_value("Website Expired Subscription",
			{"donor": row.donor, "cause": row.cause}, "name")

		if existing_name:
			frappe.db.set_value("Website Expired Subscription", existing_name, {
				"expired_date": sub.to_date,
				"total_amount": total_pending,
				"donation_left_month": pending_from,
				"months_left": months_left,
				"last_subscription": sub.name,
				"donor_subscription_type": sub.type
			}, update_modified=False)
		else:
			new_exp = frappe.get_doc({
				"doctype": "Website Expired Subscription",
				"donor": row.donor,
				"cause": row.cause,
				"expired_date": sub.to_date,
				"total_amount": total_pending,
				"donation_left_month": pending_from,
				"months_left": months_left,
				"last_subscription": sub.name,
				"donor_subscription_type": sub.type
			})
			new_exp.insert(ignore_permissions=True)

		processed.add((row.donor, row.cause))

	# Also update cause club pending info on donor
	for (donor_id, cause) in processed:
		try:
			d = frappe.get_doc("Website Donor", donor_id)
			for club_row in d.get("club_details", []):
				if club_row.cause == cause:
					pending = frappe.db.get_value("Website Expired Subscription",
						{"donor": donor_id, "cause": cause},
						["months_left", "total_amount"], as_dict=True)
					if pending:
						club_row.pending_months = pending.months_left
						club_row.pending_amount = pending.total_amount
			d.save(ignore_permissions=True)
		except Exception:
			pass

	frappe.db.commit()
