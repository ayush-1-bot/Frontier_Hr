// Short Leave Request — fill in who is asking, and show what is left.
frappe.ui.form.on("Short Leave Request", {
	onload(frm) {
		if (!frm.is_new()) return;

		if (!frm.doc.date) frm.set_value("date", frappe.datetime.get_today());

		// Default to the person filing it. HR can still pick anyone; for
		// everyone else the server refuses a request in someone else's name,
		// so leaving this blank would only produce a late error.
		if (frm.doc.employee) return;
		frappe.db.get_value(
			"Employee",
			{ user_id: frappe.session.user, status: "Active" },
			"name",
			(r) => r && r.name && frm.set_value("employee", r.name),
		);
	},

	employee: (frm) => show_balance(frm),
	date: (frm) => show_balance(frm),
	refresh: (frm) => show_balance(frm),
});

function show_balance(frm) {
	frm.dashboard.clear_headline();
	if (!frm.doc.employee || !frm.doc.date) return;

	frappe.call({
		method: "frontier_hr.short_leave.api.get_balance",
		args: { employee: frm.doc.employee, date: frm.doc.date },
		callback(r) {
			const b = r.message;
			if (!b) {
				frm.dashboard.set_headline(
					__("No Short Leave Policy covers this employee on this date."),
					"orange",
				);
				return;
			}
			frm.dashboard.set_headline(
				__("{0} of {1} short leaves left ({2} hours) between {3} and {4}", [
					b.remaining,
					b.allowed,
					b.remaining_hours,
					frappe.datetime.str_to_user(b.period_start),
					frappe.datetime.str_to_user(b.period_end),
				]),
				b.remaining > 0 ? "blue" : "orange",
			);
		},
	});
}
