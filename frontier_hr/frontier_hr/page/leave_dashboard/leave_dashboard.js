frappe.pages["leave-dashboard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({ parent: wrapper, title: __("Leave Dashboard"), single_column: true });
	const esc = (v) => frappe.utils.escape_html(v || "");
	const STATUS = {
		Open: { label: __("Pending"), color: "#f5c542", text: "#3d3200", indicator: "yellow" },
		Approved: { label: __("Approved"), color: "#2f9e5b", text: "#fff", indicator: "green" },
		Rejected: { label: __("Rejected"), color: "#e03131", text: "#fff", indicator: "red" },
		Cancelled: { label: __("Cancelled"), color: "#98a2b3", text: "#fff", indicator: "gray" },
	};
	frappe.require("/assets/frontier_hr/js/calendar_dashboard.js", () => {
		wrapper.dashboard = new frontier_hr.CalendarDashboard(page, {
			doctype: "Leave Application",
			method: "frontier_hr.frontier_hr.page.leave_dashboard.leave_dashboard.get_dashboard_data",
			statuses: STATUS,
			status_of: (e) => e.status,
			filters: [
				{ fieldname: "employee", label: __("Employee"), fieldtype: "Link", options: "Employee" },
				{ fieldname: "department", label: __("Department"), fieldtype: "Link", options: "Department" },
				{ fieldname: "leave_type", label: __("Leave Type"), fieldtype: "Link", options: "Leave Type" },
			],
			cards: [
				["total", __("Total Leave Applications"), "var(--blue-500)", ""],
				["open", __("Pending"), STATUS.Open.color, "Open"],
				["approved", __("Approved"), STATUS.Approved.color, "Approved"],
				["rejected", __("Rejected"), STATUS.Rejected.color, "Rejected"],
				["on_leave_today", __("Employees on Leave Today"), "var(--purple-500)", null],
			],
			title: (e) => e.employee_name + (e.half_day ? " (½)" : ""),
			tooltip: (e) =>
				`${e.employee_name} (${e.employee})\n${e.leave_type} · ${STATUS[e.status]?.label || e.status}\n${e.from_date} → ${e.to_date} (${e.total_leave_days} ${__("days")})`,
			columns: [
				[__("Employee"), frontier_hr.CalendarDashboard.employee_cell],
				[__("Department"), (e) => esc(e.department)],
				[__("Leave Type"), (e) => esc(e.leave_type) + (e.half_day ? " (½)" : "")],
				[__("Period"), (e) => `${frappe.datetime.str_to_user(e.from_date)} → ${frappe.datetime.str_to_user(e.to_date)}`],
			],
		});
		wrapper.dashboard.on_show();
	});
};

frappe.pages["leave-dashboard"].on_page_show = function (wrapper) {
	wrapper.dashboard && wrapper.dashboard.on_show();
};
