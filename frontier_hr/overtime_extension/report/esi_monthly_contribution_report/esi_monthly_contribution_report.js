frappe.query_reports["ESI Monthly Contribution Report"] = {
	filters: [
		{
			fieldname: "month",
			label: "Month",
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
			description: "Any date within the wage month to report on",
		},
		{
			fieldname: "employee",
			label: "Employee",
			fieldtype: "Link",
			options: "Employee",
		},
	],
};
