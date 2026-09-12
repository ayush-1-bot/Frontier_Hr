// Copyright (c) 2026, Frontier Softech
// For license information, please see license.txt

frappe.query_reports["Overtime Summary"] = {
	filters: [
		{
			fieldname: "view_by",
			label: __("View By"),
			fieldtype: "Select",
			options: "Day\nWeek\nQuarter",
			default: "Day",
			reqd: 1,
			on_change: function () {
				frappe.query_report.refresh();
			},
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "month",
			label: __("Month"),
			fieldtype: "Select",
			options: [
				"Jan", "Feb", "Mar", "Apr", "May", "Jun",
				"Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
			].join("\n"),
			default: frappe.datetime.str_to_obj(frappe.datetime.get_today())
				.toLocaleString("en-US", { month: "short" }),
			depends_on: "eval:doc.view_by=='Day'",
			mandatory_depends_on: "eval:doc.view_by=='Day'",
		},
		{
			fieldname: "year",
			label: __("Year"),
			fieldtype: "Int",
			default: new Date().getFullYear(),
			depends_on: "eval:doc.view_by=='Day'",
			mandatory_depends_on: "eval:doc.view_by=='Day'",
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			depends_on: "eval:doc.view_by=='Week'",
			mandatory_depends_on: "eval:doc.view_by=='Week'",
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_end(),
			depends_on: "eval:doc.view_by=='Week'",
			mandatory_depends_on: "eval:doc.view_by=='Week'",
		},
		{
			fieldname: "payroll_period",
			label: __("Payroll Period"),
			fieldtype: "Link",
			options: "Payroll Period",
			depends_on: "eval:doc.view_by=='Quarter'",
			mandatory_depends_on: "eval:doc.view_by=='Quarter'",
		},
		{
			fieldname: "department",
			label: __("Department"),
			fieldtype: "MultiSelectList",
			get_data: function (txt) {
				return frappe.db.get_link_options("Department", txt);
			},
		},
		{
			fieldname: "employee",
			label: __("Employee"),
			fieldtype: "MultiSelectList",
			get_data: function (txt) {
				return frappe.db.get_link_options("Employee", txt);
			},
		},
		{
			fieldname: "branch",
			label: __("Branch"),
			fieldtype: "MultiSelectList",
			get_data: function (txt) {
				return frappe.db.get_link_options("Branch", txt);
			},
		},
		{
			fieldname: "designation",
			label: __("Designation"),
			fieldtype: "MultiSelectList",
			get_data: function (txt) {
				return frappe.db.get_link_options("Designation", txt);
			},
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "MultiSelectList",
			default: ["Present", "Half Day", "Work From Home"],
			get_data: function () {
				return ["Present", "Absent", "Half Day", "Work From Home", "On Leave"]
					.map((v) => ({ value: v, description: "" }));
			},
		},
	],
};
