// Copyright (c) 2026, Frontier Softech
// For license information, please see license.txt

frappe.query_reports["Late Coming and Early Out Deduction"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_end(),
			reqd: 1,
		},
		{ fieldname: "employee", label: __("Employee"), fieldtype: "Link", options: "Employee" },
		{ fieldname: "department", label: __("Department"), fieldtype: "Link", options: "Department" },
		{ fieldname: "branch", label: __("Branch"), fieldtype: "Link", options: "Branch" },
		{ fieldname: "shift", label: __("Shift"), fieldtype: "Link", options: "Shift Type" },
		{
			fieldname: "policy",
			label: __("Policy"),
			fieldtype: "Link",
			options: "Late Coming and Early Out Deduction Policy",
		},
		{
			fieldname: "mode",
			label: __("Mode"),
			fieldtype: "Select",
			// Blank shows every judged day, whether it was charged or not.
			options: ["", "Deducted", "Report Only", "Within Grace"].join("\n"),
		},
	],

	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		if (column.fieldname === "mode" && data) {
			const colour = { Deducted: "red", "Report Only": "orange", "Within Grace": "green" }[data.mode];
			return `<span class="indicator-pill ${colour}">${__(data.mode)}</span>`;
		}

		// A day the grace swallowed: raw minutes above zero, counted minutes zero.
		if (
			data &&
			((column.fieldname === "late_raw" && data.late_raw > 0 && !data.late) ||
				(column.fieldname === "early_raw" && data.early_raw > 0 && !data.early))
		) {
			return `<span style="color: var(--text-muted)">${value}</span>`;
		}

		return value;
	},
};
