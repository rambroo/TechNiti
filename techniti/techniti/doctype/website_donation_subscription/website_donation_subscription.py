# Copyright (c) 2024, TechNiti and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, today, add_days, add_to_date, get_first_day, date_diff


class WebsiteDonationSubscription(Document):

	def before_save(self):
		if self.is_new():
			self._set_start_month()

		self._calculate_cost()
		self._generate_donation_details()
		self._set_to_date()

	def after_save(self):
		self._update_status()
		self._sync_cause_club_on_donor()
		if not self.is_new():
			self._sync_linked_donations()
		self._create_linked_donation()

	def before_delete(self):
		# Delete linked Website Donation records
		linked_donations = frappe.get_all("Website Donation",
			filters={"subscription": self.name}, pluck="name")
		for d in linked_donations:
			frappe.delete_doc("Website Donation", d, ignore_permissions=True)

		# Delete linked Website Expired Subscription records
		expired_subs = frappe.get_all("Website Expired Subscription",
			filters={"last_subscription": self.name}, pluck="name")
		for e in expired_subs:
			frappe.delete_doc("Website Expired Subscription", e, ignore_permissions=True)

	def _set_start_month(self):
		"""Determine start_month based on donor's last subscription for same cause and pause logic"""
		donor_doc = frappe.get_doc("Website Donor", self.donor)
		pause_date = getdate(donor_doc.pause_date) if getattr(donor_doc, 'pause_date', None) else None
		today_date = getdate(today())

		last_sub = frappe.db.sql("""
			SELECT to_date FROM `tabWebsite Donation Subscription`
			WHERE donor = %s AND cause = %s AND name != %s
			ORDER BY to_date DESC LIMIT 1
		""", (self.donor, self.cause or "", self.name or ""), as_dict=True)

		if pause_date:
			if today_date < pause_date:
				# Donor is currently paused — start after pause ends
				next_day = add_days(pause_date, 1)
				self.start_month = get_first_day(next_day)
				self.additional_note = "Subscription will start after pause period ends"
			elif last_sub:
				last_to_date = getdate(last_sub[0].to_date)
				if last_to_date < pause_date <= today_date:
					next_day = add_days(pause_date, 1)
					self.start_month = get_first_day(next_day)
					if donor_doc.subscription_status == "Paused":
						frappe.db.set_value("Website Donor", self.donor,
							"subscription_status", "Active", update_modified=False)
					self.additional_note = "Subscription reactivated after pause period"
				else:
					new_start = add_days(last_to_date, 1)
					self.start_month = get_first_day(new_start)
			else:
				if pause_date <= today_date:
					next_day = add_days(pause_date, 1)
					self.start_month = get_first_day(next_day)
					if donor_doc.subscription_status == "Paused":
						frappe.db.set_value("Website Donor", self.donor,
							"subscription_status", "Active", update_modified=False)
		elif last_sub:
			last_to_date = getdate(last_sub[0].to_date)
			new_start = add_days(last_to_date, 1)
			self.start_month = get_first_day(new_start)
		# else: start_month stays as whatever was set (new donor, no previous sub)

	def _calculate_cost(self):
		"""Calculate monthly cost from total_amount / number_of_months"""
		if self.type == "Unfixed":
			self.cost = None
		else:
			if self.number_of_months and self.total_amount and self.number_of_months > 0:
				self.cost = round(float(self.total_amount) / int(self.number_of_months), 2)
			else:
				self.cost = 0

	def _generate_donation_details(self):
		"""Generate month-by-month breakdown in the child table"""
		self.donation_details = []
		if not self.start_month or not self.number_of_months:
			return

		current_date = getdate(self.start_month)
		for _ in range(int(self.number_of_months)):
			end_date = add_to_date(current_date, months=1, days=-1)
			self.append("donation_details", {
				"plan": f"Monthly Donation{(' - ' + self.cause) if self.cause else ''}",
				"donation_date": current_date,
				"end_date": end_date,
				"cost": self.cost
			})
			current_date = add_to_date(current_date, months=1)

	def _set_to_date(self):
		"""Set to_date as the last day of the last month"""
		if self.start_month and self.number_of_months:
			end = add_to_date(getdate(self.start_month), months=int(self.number_of_months))
			self.to_date = add_days(end, -1)
		else:
			self.to_date = None

	def _update_status(self):
		"""Update status based on to_date"""
		if not self.to_date:
			return
		today_date = getdate(today())
		to_date = getdate(self.to_date)
		days_left = date_diff(to_date, today_date)
		if days_left < 0:
			new_status = "Expired"
		elif days_left <= 30:
			new_status = "Expiring Soon"
		else:
			new_status = "On Processing"

		if self.status != new_status:
			frappe.db.set_value("Website Donation Subscription", self.name,
				"status", new_status, update_modified=False)

	def _sync_cause_club_on_donor(self):
		"""Update the club_details child table on the Website Donor"""
		if not self.cause or not self.cost:
			return
		try:
			donor_doc = frappe.get_doc("Website Donor", self.donor)
			existing_row = None
			for row in donor_doc.get("club_details", []):
				if row.cause == self.cause:
					existing_row = row
					break

			today_date = getdate(today())
			to_date = getdate(self.to_date) if self.to_date else today_date
			status = "Active" if to_date >= today_date else "Expired"

			if existing_row:
				existing_row.monthly_club_amount = self.cost
				existing_row.last_subscription = self.name
				existing_row.last_donation_date = self.from_date
				existing_row.status = status
			else:
				donor_doc.append("club_details", {
					"cause": self.cause,
					"monthly_club_amount": self.cost,
					"last_subscription": self.name,
					"last_donation_date": self.from_date,
					"status": status
				})

			donor_doc.save(ignore_permissions=True)
		except Exception:
			pass

	def _create_linked_donation(self):
		"""Auto-create a linked Website Donation if none exists"""
		existing = frappe.get_all("Website Donation",
			filters={"subscription": self.name}, limit=1)
		if existing:
			return

		new_donation = frappe.new_doc("Website Donation")
		new_donation.donor = self.donor
		new_donation.receipt_donor_check = self.receipt_donor_check
		new_donation.receipt_donor = self.receipt_donor if not self.receipt_donor_check else self.donor
		new_donation.donation_date = self.from_date
		new_donation.amount = self.total_amount
		new_donation.mode_of_payment = self.mode_of_payment
		new_donation.cause = self.cause
		new_donation.is_club_donation = 1
		new_donation.subscription = self.name
		new_donation.is_company_donation = self.is_company_donation
		new_donation.company_name = self.company_name
		new_donation.sub_donor = self.sub_donor
		new_donation.payment_status = self.payment_status
		new_donation.razorpay_order_id = self.razorpay_order_id
		new_donation.razorpay_payment_id = self.razorpay_payment_id
		new_donation.insert(ignore_permissions=True)

	def _sync_linked_donations(self):
		"""Sync changes to linked Website Donation records"""
		linked_donations = frappe.get_all("Website Donation",
			filters={"subscription": self.name}, pluck="name")
		for donation_name in linked_donations:
			try:
				d = frappe.get_doc("Website Donation", donation_name)
				d.donor = self.donor
				d.mode_of_payment = self.mode_of_payment
				d.donation_date = self.from_date
				d.amount = self.total_amount
				d.number_of_months = self.number_of_months
				d.receipt_donor_check = self.receipt_donor_check
				d.receipt_donor = self.receipt_donor if not self.receipt_donor_check else self.donor
				d.cause = self.cause
				d.save(ignore_permissions=True)
			except Exception:
				pass
