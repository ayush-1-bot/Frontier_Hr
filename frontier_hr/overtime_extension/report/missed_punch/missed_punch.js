frappe.query_reports["Missed Punch"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_days(frappe.datetime.month_start(), 0),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_end(),
			reqd: 1,
		},
		{
			fieldname: "employee",
			label: __("Employee"),
			fieldtype: "Link",
			options: "Employee",
		},
		{
			fieldname: "shift",
			label: __("Shift"),
			fieldtype: "Link",
			options: "Shift Type",
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
		},
		{
			fieldname: "branch",
			label: __("Branch"),
			fieldtype: "MultiSelectList",
			// empty = all branches
			get_data: (txt) => frappe.db.get_link_options("Branch", txt),
		},
		{
			fieldname: "issue",
			label: __("Issue"),
			fieldtype: "Select",
			options: [
				"",
				"Only One Punch",
				"Missing In/Out",
				"No Attendance Marked",
			].join("\n"),
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "issue" && data && data.issue) {
			value = `<span style="color:var(--red-600)">${value}</span>`;
		}
		return value;
	},
};
