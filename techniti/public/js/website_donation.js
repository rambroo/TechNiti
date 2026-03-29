frappe.ui.form.on('Website Donation', {
	refresh(frm) {
		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(__('Regenerate Receipt PDF'), function () {
				frappe.call({
					method: 'techniti.donation_pdf.regenerate_donation_pdf',
					args: { docname: frm.doc.name },
					callback: function () {
						// Reload after a short delay to pick up the updated custom_pdf_url
						setTimeout(() => frm.reload_doc(), 4000);
					}
				});
			}, __('Actions'));

			// Clickable PDF link in the form
			if (frm.doc.custom_pdf_url) {
				frm.add_custom_button(__('View Receipt PDF'), function () {
					window.open(frm.doc.custom_pdf_url, '_blank');
				}, __('Actions'));
			}
		}
	}
});
