frappe.pages["attendance-request-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Attendance Request Dashboard"), single_column: true });
	const esc = (v) => frappe.utils.escape_html(v || "");
	const STATUS = {
		Pending: { label: __("Pending"), color: "#f5c542", text: "#3d3200", indicator: "yellow" },
		Approved: { label: __("Approved"), color: "#2f9e5b", text: "#fff", indicator: "green" },
		Cancelled: { label: __("Cancelled"), color: "#98a2b3", text: "#fff", indicator: "gray" },
	};
	const status_of = (e) => ["Pending", "Approved", "Cancelled"][e.docstatus];
	const dates = (e) =>
		e.from_date === e.to_date
			? frappe.datetime.str_to_user(e.from_date)
			: `${frappe.datetime.str_to_user(e.from_date)} → ${frappe.datetime.str_to_user(e.to_date)}`;
	// single punch -> "IN 09:02"; several -> "IN 08:56 · OUT 13:00 · IN 13:30 · OUT 17:34"
	const punches = (e, date) =>
		((e.punches || {})[date] || []).map(([t, type]) => (type ? `${type} ${t}` : t)).join(" · ");
	const half = (e) => (e.half_day ? ` (½${e.half_day_date ? " " + frappe.datetime.str_to_user(e.half_day_date) : ""})` : "");

	frappe.require("/assets/frontier_hr/js/calendar_dashboard.js", () => {
		wrapper.dashboard = new frontier_hr.CalendarDashboard(page, {
			doctype: "Attendance Request",
			method: "frontier_hr.frontier_hr.page.attendance_request_dashboard.attendance_request_dashboard.get_dashboard_data",
			statuses: STATUS,
			status_of,
			split_days: true,
			subtitle: (e, date) => punches(e, date) || __("No punch"),
			filters: [
				{ fieldname: "employee", label: __("Employee"), fieldtype: "Link", options: "Employee" },
				{ fieldname: "department", label: __("Department"), fieldtype: "Link", options: "Department" },
				{
					fieldname: "reason", label: __("Request Type"), fieldtype: "Select",
					options: [{ value: "", label: __("All Request Types") }, { value: "Work From Home", label: __("Work From Home") }, { value: "On Duty", label: __("On Duty") }],
				},
			],
			cards: [
				["total", __("Total Attendance Requests"), "var(--blue-500)", ""],
				["pending", __("Pending"), STATUS.Pending.color, "Pending"],
				["approved", __("Approved"), STATUS.Approved.color, "Approved"],
				["cancelled", __("Cancelled"), STATUS.Cancelled.color, "Cancelled"],
				["today", __("Attendance Requests for Today"), "var(--purple-500)", null],
			],
			title: (e) => `${e.employee_name} · ${e.reason === "On Duty" ? __("OD") : __("WFH")}` + (e.half_day ? " (½)" : ""),
			tooltip: (e) =>
				`${e.employee_name} (${e.employee})\n${e.reason} · ${STATUS[status_of(e)].label}\n${dates(e)}${half(e)}` +
				(e.shift ? `\n${__("Shift")}: ${e.shift}` : "") + (e.explanation ? `\n${e.explanation}` : ""),
			columns: [
				[__("Employee"), frontier_hr.CalendarDashboard.employee_cell],
				[__("Department"), (e) => esc(e.department)],
				[__("Request Type"), (e) => esc(e.reason)],
				[__("Attendance Date"), (e) => dates(e) + half(e)],
				[__("Shift"), (e) => esc(e.shift)],
				[__("Punches"), (e, date) => esc(punches(e, date)) || `<span class="text-muted">${__("No punch")}</span>`],
				[__("Reason"), (e) => esc(e.explanation)],
			],
		});
		wrapper.dashboard.on_show();
	});
};

frappe.pages["attendance-request-dashboard"].on_page_show = function (wrapper) {
	wrapper.dashboard && wrapper.dashboard.on_show();
};
