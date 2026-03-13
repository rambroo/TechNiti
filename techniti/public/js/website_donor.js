// Website Donor — desk client script
// Loaded via hooks.py: doctype_js = {"Website Donor": "public/js/website_donor.js"}

frappe.ui.form.on('Website Donor', {

	refresh(frm) {
		// ── Quick-nav buttons ──────────────────────────────────────
		frm.add_custom_button(__('View Donations'), function() {
			frappe.set_route('List', 'Website Donation', { donor: frm.doc.name });
		}, __('Go To'));

		frm.add_custom_button(__('View Subscriptions'), function() {
			frappe.set_route('List', 'Website Donation Subscription', { donor: frm.doc.name });
		}, __('Go To'));

		frm.add_custom_button(__('View Expired Subscriptions'), function() {
			frappe.set_route('List', 'Website Expired Subscription', { donor: frm.doc.name });
		}, __('Go To'));

		// ── Linked Records tab — load inline summary ───────────────
		if (!frm.is_new()) {
			load_linked_records(frm);
		}

		// ── Club Details tab description ───────────────────────────
		frm.set_intro(
			'To change a donor\'s club contribution, edit the <b>Club Details</b> tab and save. ' +
			'The updated amount will apply on their next donation.',
			'blue'
		);
	},

	after_save(frm) {
		// Reload linked records after save (club changes may create new subs)
		if (!frm.is_new()) {
			load_linked_records(frm);
		}
	}
});


function load_linked_records(frm) {
	const container = document.getElementById('linked-records-container');
	if (!container) return;

	container.innerHTML = '<p style="color:#aaa;font-size:13px;">Loading...</p>';

	// Fetch donations
	frappe.call({
		method: 'frappe.client.get_list',
		args: {
			doctype: 'Website Donation',
			filters: { donor: frm.doc.name },
			fields: ['name', 'donation_date', 'amount', 'payment_status', 'cause', 'is_club_donation'],
			order_by: 'donation_date desc',
			limit_page_length: 10
		},
		callback: function(donations_res) {
			frappe.call({
				method: 'frappe.client.get_list',
				args: {
					doctype: 'Website Donation Subscription',
					filters: { donor: frm.doc.name },
					fields: ['name', 'cause', 'from_date', 'to_date', 'status', 'cost', 'total_amount', 'number_of_months'],
					order_by: 'from_date desc',
					limit_page_length: 10
				},
				callback: function(subs_res) {
					render_linked_records(container, frm.doc.name,
						donations_res.message || [],
						subs_res.message || []
					);
				}
			});
		}
	});
}


function render_linked_records(container, donor_name, donations, subscriptions) {
	const fmt_currency = (v) => v != null ? '₹' + parseFloat(v).toLocaleString('en-IN', { maximumFractionDigits: 0 }) : '—';
	const fmt_date = (d) => d ? frappe.datetime.str_to_user(d) : '—';

	const status_color = {
		'Captured': 'green', 'Pending': 'orange', 'Failed': 'red',
		'On Processing': 'green', 'Expiring Soon': 'orange', 'Expired': 'red'
	};

	// Donations table
	let don_rows = donations.map(d => `
		<tr>
			<td><a href="/app/website-donation/${d.name}" target="_blank">${d.name}</a></td>
			<td>${fmt_date(d.donation_date)}</td>
			<td>${d.cause || '—'}</td>
			<td><strong>${fmt_currency(d.amount)}</strong></td>
			<td><span class="indicator-pill ${status_color[d.payment_status] || 'gray'}">${d.payment_status}</span></td>
			<td>${d.is_club_donation ? '<span style="color:green;font-weight:600;">Club</span>' : 'One-Time'}</td>
		</tr>
	`).join('');

	if (!don_rows) don_rows = '<tr><td colspan="6" style="color:#aaa;text-align:center;">No donations found</td></tr>';

	// Subscriptions table
	let sub_rows = subscriptions.map(s => `
		<tr>
			<td><a href="/app/website-donation-subscription/${s.name}" target="_blank">${s.name}</a></td>
			<td>${s.cause || '—'}</td>
			<td>${fmt_date(s.from_date)}</td>
			<td>${fmt_date(s.to_date)}</td>
			<td>${s.number_of_months || '—'} mo</td>
			<td>${fmt_currency(s.cost)}/mo</td>
			<td>${fmt_currency(s.total_amount)}</td>
			<td><span class="indicator-pill ${status_color[s.status] || 'gray'}">${s.status}</span></td>
		</tr>
	`).join('');

	if (!sub_rows) sub_rows = '<tr><td colspan="8" style="color:#aaa;text-align:center;">No subscriptions found</td></tr>';

	const table_style = 'width:100%;border-collapse:collapse;font-size:13px;margin-bottom:24px;';
	const th_style = 'padding:8px 10px;text-align:left;font-weight:700;color:#666;font-size:11px;text-transform:uppercase;letter-spacing:0.4px;border-bottom:2px solid #f0f0f0;';
	const td_style = 'padding:8px 10px;border-bottom:1px solid #f5f5f5;';

	container.innerHTML = `
		<h4 style="font-size:14px;font-weight:700;color:#333;margin-bottom:12px;">
			Recent Donations
			<a href="/app/website-donation?donor=${donor_name}" target="_blank"
			   style="font-size:12px;font-weight:400;color:#3cc88f;margin-left:8px;">View all →</a>
		</h4>
		<div style="overflow-x:auto;">
			<table style="${table_style}">
				<thead>
					<tr>
						<th style="${th_style}">ID</th>
						<th style="${th_style}">Date</th>
						<th style="${th_style}">Cause</th>
						<th style="${th_style}">Amount</th>
						<th style="${th_style}">Status</th>
						<th style="${th_style}">Type</th>
					</tr>
				</thead>
				<tbody>${don_rows}</tbody>
			</table>
		</div>

		<h4 style="font-size:14px;font-weight:700;color:#333;margin-bottom:12px;margin-top:8px;">
			Subscriptions
			<a href="/app/website-donation-subscription?donor=${donor_name}" target="_blank"
			   style="font-size:12px;font-weight:400;color:#3cc88f;margin-left:8px;">View all →</a>
		</h4>
		<div style="overflow-x:auto;">
			<table style="${table_style}">
				<thead>
					<tr>
						<th style="${th_style}">ID</th>
						<th style="${th_style}">Cause</th>
						<th style="${th_style}">From</th>
						<th style="${th_style}">To</th>
						<th style="${th_style}">Months</th>
						<th style="${th_style}">Monthly</th>
						<th style="${th_style}">Total</th>
						<th style="${th_style}">Status</th>
					</tr>
				</thead>
				<tbody>${sub_rows}</tbody>
			</table>
		</div>
	`;
}
