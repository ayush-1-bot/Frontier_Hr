console.log("OT Client Script Loaded ✅");

frappe.ui.form.on('Overtime Slip', {

    employee: function (frm) {
        if (!frm.doc.employee) return;
        let today = frappe.datetime.get_today();
        let d = frappe.datetime.str_to_obj(today);
        let year  = d.getFullYear();
        let month = d.getMonth();
        // First to last day of current month
        let last_day = new Date(year, month + 1, 0).getDate();
        frm.set_value('start_date', frappe.datetime.obj_to_str(new Date(year, month, 1)));
        frm.set_value('end_date',   frappe.datetime.obj_to_str(new Date(year, month, last_day)));
    },

    refresh: function (frm) {
        if (frm.doc.docstatus === 0) {
            frm.add_custom_button(__('Fetch OT from Attendance'), function () {
                _fetch_ot(frm);
            }, __('Actions'));
        }
    },
});

function _fetch_ot(frm) {
    if (!frm.doc.employee || !frm.doc.start_date || !frm.doc.end_date) {
        frappe.msgprint(__('Please set Employee, Start Date and End Date first.'));
        return;
    }
    frappe.call({
        method: 'frontier_hr.overtime_extension.overrides.overtime_slip.fetch_ot_from_attendance',
        args: {
            employee:   frm.doc.employee,
            start_date: frm.doc.start_date,
            end_date:   frm.doc.end_date,
        },
        freeze: true,
        freeze_message: __('Fetching OT hours from Attendance...'),
        callback: function (r) {
            if (!r.message) return;
            let data = r.message;
            if (!data.rows || data.rows.length === 0) {
                frappe.msgprint(__('No OT hours found in Attendance for the selected period.'));
                return;
            }
            frm.clear_table('overtime_details');
            data.rows.forEach(function (row) {
                let child = frm.add_child('overtime_details');
                child.date                   = row.date;
                child.overtime_type          = row.overtime_type || '';
                child.overtime_duration      = row.ot_hours;
                child.standard_working_hours = row.standard_working_hours;
            });
            frm.refresh_field('overtime_details');
            frm.set_value('custom_actual_ot_hours',  data.actual_total);
            frm.set_value('custom_payable_ot_hours', data.payable_total);
            let diff = (data.actual_total - data.payable_total).toFixed(2);
            let msg  = `
                <b>OT Fetch Complete</b><br>
                Actual OT Worked: <b>${data.actual_total} hrs</b><br>
                Payable OT (after caps): <b>${data.payable_total} hrs</b>
            `;
            if (diff > 0) {
                msg += `<br><span style="color:orange;">⚠ ${diff} hrs capped due to weekly/quarterly limit.</span>`;
            }
            frappe.msgprint({ message: msg, title: 'OT Summary', indicator: 'blue' });
        },
    });
}
