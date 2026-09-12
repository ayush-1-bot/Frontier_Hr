# Copyright (c) 2025, Frontier Softech
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import getdate
from collections import defaultdict


def execute(filters=None):
    filters = frappe._dict(filters or {})
    if not filters.get("from_date") or not filters.get("to_date"):
        return get_columns(filters), [], None, None, None

    validate_filters(filters)

    columns = get_columns(filters)
    data = get_data(filters)
    raw_logs = get_raw_logs(filters)

    chart = get_chart_data(filters, raw_logs) if filters.get("show_charts") else None
    summary = get_summary(data, raw_logs) if filters.get("show_summary") else None

    # Hide detail table if user unchecks it
    if not filters.get("show_detail_table"):
        data = []

    return columns, data, None, chart, summary


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_filters(filters):
    if not filters.from_date or not filters.to_date:
        frappe.throw(_("From Date and To Date are required"))
    if getdate(filters.from_date) > getdate(filters.to_date):
        frappe.throw(_("From Date cannot be after To Date"))


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------
def get_columns(filters):
    return [
        {"label": _("Employee"), "fieldname": "employee", "fieldtype": "Link", "options": "Employee", "width": 120},
        {"label": _("Employee Name"), "fieldname": "employee_name", "fieldtype": "Data", "width": 160},
        {"label": _("Department"), "fieldname": "department", "fieldtype": "Link", "options": "Department", "width": 140},
        {"label": _("Designation"), "fieldname": "designation", "fieldtype": "Link", "options": "Designation", "width": 130},
        {"label": _("Shift"), "fieldname": "shift", "fieldtype": "Link", "options": "Shift Type", "width": 110},
        {"label": _("Date"), "fieldname": "date", "fieldtype": "Date", "width": 100},
        {"label": _("First IN"), "fieldname": "first_in", "fieldtype": "Time", "width": 100},
        {"label": _("Last OUT"), "fieldname": "last_out", "fieldtype": "Time", "width": 100},
        {"label": _("Device ID"), "fieldname": "device_id", "fieldtype": "Data", "width": 110},
    ]


# ---------------------------------------------------------------------------
# Data query
# ---------------------------------------------------------------------------
def get_data(filters):
    conditions, values = build_conditions(filters)

    query = f"""
        SELECT
            ec.employee,
            ec.employee_name,
            emp.department,
            emp.designation,
            emp.branch,
            ec.shift,
            DATE(ec.time) AS date,
            TIME(MIN(CASE WHEN ec.log_type = 'IN' THEN ec.time END)) AS first_in,
            TIME(MAX(CASE WHEN ec.log_type = 'OUT' THEN ec.time END)) AS last_out,
            ec.device_id,
            ec.attendance
        FROM `tabEmployee Checkin` ec
        LEFT JOIN `tabEmployee` emp ON emp.name = ec.employee
        WHERE DATE(ec.time) BETWEEN %(from_date)s AND %(to_date)s
        {conditions}
        GROUP BY ec.employee, DATE(ec.time), ec.shift
        ORDER BY ec.employee, DATE(ec.time)
    """

    values.update({
        "from_date": filters.from_date,
        "to_date": filters.to_date,
    })

    return frappe.db.sql(query, values, as_dict=True)


def get_raw_logs(filters):
    """Individual checkin log rows (log_type, date, department) used for IN/OUT split KPIs and charts."""
    conditions, values = build_conditions(filters)

    query = f"""
        SELECT
            ec.log_type,
            DATE(ec.time) AS date,
            emp.department
        FROM `tabEmployee Checkin` ec
        LEFT JOIN `tabEmployee` emp ON emp.name = ec.employee
        WHERE DATE(ec.time) BETWEEN %(from_date)s AND %(to_date)s
        {conditions}
    """

    values.update({
        "from_date": filters.from_date,
        "to_date": filters.to_date,
    })

    return frappe.db.sql(query, values, as_dict=True)


def build_conditions(filters):
    parts = []
    values = {}

    if filters.get("company"):
        parts.append("AND emp.company = %(company)s")
        values["company"] = filters.company
    if filters.get("employee"):
        parts.append("AND ec.employee = %(employee)s")
        values["employee"] = filters.employee
    if filters.get("department"):
        parts.append("AND emp.department = %(department)s")
        values["department"] = filters.department
    if filters.get("branch"):
        parts.append("AND emp.branch = %(branch)s")
        values["branch"] = filters.branch
    if filters.get("designation"):
        parts.append("AND emp.designation = %(designation)s")
        values["designation"] = filters.designation
    if filters.get("shift"):
        parts.append("AND ec.shift = %(shift)s")
        values["shift"] = filters.shift
    if filters.get("log_type"):
        parts.append("AND ec.log_type = %(log_type)s")
        values["log_type"] = filters.log_type
    if filters.get("attendance_marked"):
        parts.append("AND ec.attendance IS NOT NULL AND ec.attendance != ''")

    return " ".join(parts), values


# ---------------------------------------------------------------------------
# Summary KPIs
# ---------------------------------------------------------------------------
def get_summary(data, raw_logs):
    if not data:
        return []

    total_employees = len({r.employee for r in data})
    total_departments = len({r.department for r in data if r.department})
    total_in = sum(1 for r in raw_logs if r.log_type == "IN")
    total_out = sum(1 for r in raw_logs if r.log_type == "OUT")

    return [
        {"value": total_in + total_out, "label": _("Total Checkin Logs"), "datatype": "Int"},
        {"value": total_in, "label": _("Total IN Logs"), "datatype": "Int", "indicator": "Green"},
        {"value": total_out, "label": _("Total OUT Logs"), "datatype": "Int", "indicator": "Red"},
        {"value": total_employees, "label": _("Total Employees"), "datatype": "Int", "indicator": "Blue"},
        {"value": total_departments, "label": _("Total Departments"), "datatype": "Int", "indicator": "Orange"},
    ]


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
def get_chart_data(filters, raw_logs):
    if not raw_logs:
        return None

    if filters.get("show_daily_trend"):
        return daily_trend_chart(raw_logs)
    if filters.get("show_department_chart"):
        return department_chart(raw_logs)

    return None


def daily_trend_chart(raw_logs):
    daily = defaultdict(lambda: {"IN": 0, "OUT": 0})
    for r in raw_logs:
        if r.log_type in ("IN", "OUT"):
            daily[str(r.date)][r.log_type] += 1

    labels = sorted(daily.keys())
    return {
        "data": {
            "labels": labels,
            "datasets": [
                {"name": "IN", "values": [daily[d]["IN"] for d in labels]},
                {"name": "OUT", "values": [daily[d]["OUT"] for d in labels]},
            ],
        },
        "type": "bar",
        "title": _("Daily Checkin Trend (IN vs OUT)"),
        "colors": ["#28a745", "#dc3545"],
        "barOptions": {"stacked": 0},
    }


def department_chart(raw_logs):
    dept = defaultdict(lambda: {"IN": 0, "OUT": 0})
    for r in raw_logs:
        if r.log_type in ("IN", "OUT"):
            dept[r.department or "Unassigned"][r.log_type] += 1

    labels = list(dept.keys())
    return {
        "data": {
            "labels": labels,
            "datasets": [
                {"name": "IN", "values": [dept[l]["IN"] for l in labels]},
                {"name": "OUT", "values": [dept[l]["OUT"] for l in labels]},
            ],
        },
        "type": "bar",
        "title": _("Department-wise Checkin Logs (IN vs OUT)"),
        "colors": ["#28a745", "#dc3545"],
    }
