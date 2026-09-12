// Copyright (c) 2025, Frontier Softech
// For license information, please see license.txt

frappe.query_reports["Employee Checkin Analysis"] = {
    filters: [
        {
            fieldname: "from_date",
            label: __("From Date"),
            fieldtype: "Date",
            default: frappe.datetime.month_start(),
            reqd: 1
        },
        {
            fieldname: "to_date",
            label: __("To Date"),
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
            reqd: 1
        },
        {
            fieldname: "company",
            label: __("Company"),
            fieldtype: "Link",
            options: "Company",
            default: frappe.defaults.get_user_default("Company")
        },
        {
            fieldname: "employee",
            label: __("Employee"),
            fieldtype: "Link",
            options: "Employee"
        },
        {
            fieldname: "department",
            label: __("Department"),
            fieldtype: "Link",
            options: "Department"
        },
        {
            fieldname: "branch",
            label: __("Branch"),
            fieldtype: "Link",
            options: "Branch"
        },
        {
            fieldname: "designation",
            label: __("Designation"),
            fieldtype: "Link",
            options: "Designation"
        },
        {
            fieldname: "shift",
            label: __("Shift Type"),
            fieldtype: "Link",
            options: "Shift Type"
        },
        {
            fieldname: "log_type",
            label: __("Log Type"),
            fieldtype: "Select",
            options: "\nIN\nOUT"
        },
        {
            fieldname: "attendance_marked",
            label: __("Attendance Marked"),
            fieldtype: "Check"
        },
        {
            fieldname: "show_summary",
            label: __("Show Summary KPIs"),
            fieldtype: "Check",
            default: 1
        },
        {
            fieldname: "show_charts",
            label: __("Show Charts"),
            fieldtype: "Check",
            default: 1
        },
        {
            fieldname: "show_daily_trend",
            label: __("Show Daily Trend Chart"),
            fieldtype: "Check",
            default: 1
        },
        {
            fieldname: "show_department_chart",
            label: __("Show Department Chart"),
            fieldtype: "Check",
            default: 1
        },
        {
            fieldname: "show_detail_table",
            label: __("Show Detail Table"),
            fieldtype: "Check",
            default: 1
        }
    ],
};
