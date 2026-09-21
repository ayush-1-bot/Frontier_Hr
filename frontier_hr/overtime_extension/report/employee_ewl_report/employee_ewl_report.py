# DEPRECATED 2026-09-21: report disabled (disabled=1 in the .json) - not needed,
# and it errored (same report name also shipped by company_hr_ext). Kept on disk;
# do not delete. Re-enable by setting disabled=0 and migrating.
import calendar
from datetime import date

import frappe
from frappe.utils import getdate

# Fiscal year starts in April (Indian FY).
FY_START_MONTH = 4
MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def execute(filters=None):
    filters = filters or {}
    months = get_fy_months_upto_today()
    return get_columns(months), get_data(filters, months)


def get_fy_months_upto_today():
    """Return list of (year, month) from FY start (April) through the current month."""
    today = date.today()
    if today.month >= FY_START_MONTH:
        fy_year = today.year
    else:
        fy_year = today.year - 1

    months = []
    y, m = fy_year, FY_START_MONTH
    while (y, m) <= (today.year, today.month):
        months.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


def get_columns(months):
    cols = [
        {"label": "First Name", "fieldname": "first_name", "fieldtype": "Data", "width": 140},
        {"label": "Bank Name", "fieldname": "bank_name", "fieldtype": "Data", "width": 140},
        {"label": "IFSC Code", "fieldname": "ifsc_code", "fieldtype": "Data", "width": 120},
        {"label": "ESIC No", "fieldname": "esic_no", "fieldtype": "Data", "width": 120},
        {"label": "PF No", "fieldname": "pf_no", "fieldtype": "Data", "width": 140},
        {"label": "Date of Birth", "fieldname": "date_of_birth", "fieldtype": "Date", "width": 110},
        {"label": "Date of Joining", "fieldname": "date_of_joining", "fieldtype": "Date", "width": 110},
        {"label": "Date of Relieving", "fieldname": "date_of_relieving", "fieldtype": "Date", "width": 120},
        {"label": "Location", "fieldname": "location", "fieldtype": "Data", "width": 140},
        {"label": "Nationality(0_Punjab_1_India_2_Non-India)", "fieldname": "nationality", "fieldtype": "Data", "width": 260},
        {"label": "No_Of_Months_Employee_On_Leave(IfAny)", "fieldname": "no_of_months_on_leave", "fieldtype": "Data", "width": 240},
    ]
    for y, m in months:
        cols.append({
            "label": f"{MONTH_ABBR[m - 1]} {y}",
            "fieldname": f"m_{y}_{m:02d}",
            "fieldtype": "Int",
            "width": 90,
        })
    return cols


def get_data(filters, months):
    emp_filters = {}
    for f in ("status", "employment_type", "company", "department", "branch", "designation", "name"):
        if filters.get(f):
            emp_filters[f if f != "name" else "name"] = filters[f]

    employees = frappe.get_all(
        "Employee",
        filters=emp_filters,
        fields=[
            "name", "first_name", "employee_name",
            "bank_name", "ifsc_code", "custom_esi_no", "provident_fund_account",
            "date_of_birth", "date_of_joining", "relieving_date",
            "branch",
        ],
        order_by="employee_name asc",
    )

    if not employees:
        return []

    attendance_counts = get_attendance_counts([e.name for e in employees], months)

    data = []
    for emp in employees:
        row = {
            "first_name": emp.first_name or emp.employee_name,
            "bank_name": emp.bank_name,
            "ifsc_code": emp.ifsc_code,
            "esic_no": emp.custom_esi_no,
            "pf_no": emp.provident_fund_account,
            "date_of_birth": emp.date_of_birth,
            "date_of_joining": emp.date_of_joining,
            "date_of_relieving": emp.relieving_date,
            "location": emp.branch,
            "nationality": "",
            "no_of_months_on_leave": "",
        }
        for y, m in months:
            key = f"m_{y}_{m:02d}"
            present = attendance_counts.get((emp.name, y, m), 0)
            row[key] = 5 if present > 1 else 0
        data.append(row)
    return data


def get_attendance_counts(employee_names, months):
    """Return {(employee, year, month): present_count} for Present/Work From Home statuses."""
    if not employee_names or not months:
        return {}

    start = date(months[0][0], months[0][1], 1)
    last_y, last_m = months[-1]
    end = date(last_y, last_m, calendar.monthrange(last_y, last_m)[1])

    rows = frappe.db.sql(
        """
        SELECT employee, YEAR(attendance_date) AS y, MONTH(attendance_date) AS m,
               COUNT(*) AS cnt
        FROM `tabAttendance`
        WHERE docstatus = 1
          AND status IN ('Present', 'Work From Home')
          AND attendance_date BETWEEN %(start)s AND %(end)s
          AND employee IN %(employees)s
        GROUP BY employee, y, m
        """,
        {"start": start, "end": end, "employees": tuple(employee_names)},
        as_dict=True,
    )
    return {(r.employee, int(r.y), int(r.m)): int(r.cnt) for r in rows}
