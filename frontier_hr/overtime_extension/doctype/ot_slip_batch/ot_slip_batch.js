frappe.ui.form.on('OT Slip Batch', {
    refresh: function (frm) {
        if (frm.doc.docstatus !== 0 && frm.doc.docstatus !== undefined) return;

        // Preview button
        if (frm.doc.start_date && frm.doc.end_date && frm.doc.company) {
            frm.add_custom_button(__('Preview Employees'), function () {
                frappe.call({
                    method: 'frontier_hr.overtime_extension.doctype.ot_slip_batch.ot_slip_batch.preview_employees',
                    args: { batch_name: frm.doc.name },
                    freeze: true,
                    freeze_message: __('Fetching employees...'),
                    callback: function (r) {
                        if (r.message) {
                            frappe.show_alert({
                                message: __(`Found ${r.message.eligible} eligible / ${r.message.total} total`),
                                indicator: 'blue'
                            });
                            frm.reload_doc();
                        }
                    }
                });
            }, __('Actions'));
        }

        // Create Slips button
        if (frm.doc.status === 'Previewed') {
            frm.add_custom_button(__('Create OT Slips'), function () {
                frappe.confirm(
                    __('Create OT Slips for all eligible employees?'),
                    function () {
                        frappe.call({
                            method: 'frontier_hr.overtime_extension.doctype.ot_slip_batch.ot_slip_batch.create_slips',
                            args: { batch_name: frm.doc.name },
                            freeze: true,
                            freeze_message: __('Creating OT Slips...'),
                            callback: function (r) {
                                if (r.message) {
                                    frappe.msgprint({
                                        title: __('Done'),
                                        message: __(`Created: ${r.message.created}<br>Skipped: ${r.message.skipped}<br>Failed: ${r.message.failed}`),
                                        indicator: r.message.failed ? 'orange' : 'green'
                                    });
                                    frm.reload_doc();
                                }
                            }
                        });
                    }
                );
            }, __('Actions'));
        }

        // Cancel All button
        if (frm.doc.status === 'Completed' || frm.doc.status === 'Partial Failure') {
            frm.add_custom_button(__('Cancel All Slips'), function () {
                frappe.confirm(
                    __('Cancel all OT Slips created by this batch?'),
                    function () {
                        frappe.call({
                            method: 'frontier_hr.overtime_extension.doctype.ot_slip_batch.ot_slip_batch.cancel_batch_slips',
                            args: { batch_name: frm.doc.name },
                            freeze: true,
                            callback: function (r) {
                                if (r.message) {
                                    frappe.msgprint(__(`Cancelled ${r.message.cancelled} slips`));
                                    frm.reload_doc();
                                }
                            }
                        });
                    }
                );
            }, __('Actions'));
        }
    }
});
