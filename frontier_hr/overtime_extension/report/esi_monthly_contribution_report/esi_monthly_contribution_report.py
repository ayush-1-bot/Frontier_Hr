import math

import frappe

# ESIC Monthly Contribution (MC) upload reason codes.
# Codes 2, 3, 4, 5, 6, 10 require a Last Working Day and drop the IP from the
# next wage/contribution period. All other codes must leave Last Working Day blank.
REASON_CODES = {
    0: "Without Reason",
    1: "On Leave",
    2: "Left Service",
    3: "Retired",
    4: "Out of Coverage",
    5: "Expired",
    6: "Non Implemented Area",
    7: "Compliance by Immediate Employer",
    8: "Suspension of Work",
    9: "Strike/Lockout",
    10: "Retrenchment",
    11: "No Work",
    12: "Doesnt Belong To This Employer",
}

# Reason codes that require a Last Working Day to be reported.
CODES_REQUIRING_LWD = {2, 3, 4, 5, 6, 10}

STATUS_REASON_CODE = {
    "Suspended": 8,  # Suspension of work
    "Inactive": 12,  # Doesn't belong to this employer
}


def execute(filters=None):
    filters = filters or {}
    return get_columns(), get_data(filters)


def get_columns():
    return [
        {"label": "IP Number (PF No.)", "fieldname": "pf_number", "fieldtype": "Data", "width": 140},
        {"label": "IP Name", "fieldname": "employee_name", "fieldtype": "Data", "width": 200},
        {"label": "No of Days Wages Paid", "fieldname": "paid_days", "fieldtype": "Int", "width": 160},
        {"label": "Total Monthly Wages", "fieldname": "monthly_wages", "fieldtype": "Currency", "width": 150},
        {"label": "Reason Code", "fieldname": "reason_code", "fieldtype": "Int", "width": 110},
        {"label": "Reason Description", "fieldname": "reason_description", "fieldtype": "Data", "width": 220},
        {"label": "Last Working Day", "fieldname": "last_working_day", "fieldtype": "Date", "width": 140},
    ]


def get_reason_and_lwd(paid_days, emp, start, end):
    # Rule: last working day is mentioned ONLY when 0 days are paid/payable.
    # If the IP worked any part of the month, always report "Without Reason" with no LWD.
    if paid_days > 0:
        return 0, None

    # Left Service: only when the relieving date actually falls inside this wage period.
    if emp.status == "Left":
        rd = frappe.utils.getdate(emp.relieving_date) if emp.relieving_date else None
        if rd and start <= rd <= end:
            return 2, rd
        # Left without a valid relieving date in this period is a data issue;
        # fall back to "On Leave" rather than guessing a code that needs an LWD.
        return 1, None

    if emp.status in STATUS_REASON_CODE:
        return STATUS_REASON_CODE[emp.status], None

    # Active employee with zero wages and no exit event.
    return 1, None


def get_data(filters):
    month = filters.get("month")
    if not month:
        return []

    start = frappe.utils.getdate(month).replace(day=1)
    end = frappe.utils.get_last_day(start)

    emp_filters = {"status": ["in", ["Active", "Left", "Suspended", "Inactive"]]}
    if filters.get("employee"):
        emp_filters["name"] = filters.get("employee")

    employees = frappe.get_all(
        "Employee",
        filters=emp_filters,
        fields=["name", "custom_esi_no", "employee_name", "status", "relieving_date", "date_of_joining"],
    )

    data = []
    for emp in employees:
        if emp.date_of_joining and frappe.utils.getdate(emp.date_of_joining) > end:
            continue
        if emp.relieving_date and frappe.utils.getdate(emp.relieving_date) < start:
            continue

        ss = frappe.db.get_value(
            "Salary Slip",
            {
                "employee": emp.name,
                "start_date": [">=", start],
                "end_date": ["<=", end],
                "docstatus": 1,
            },
            ["payment_days", "gross_pay"],
        )
        # ESIC rule: number of days must be a whole number; fractions are rounded UP.
        paid_days = math.ceil(frappe.utils.flt(ss[0])) if ss else 0
        wages = frappe.utils.flt(ss[1]) if ss else 0

        reason_code, lwd = get_reason_and_lwd(paid_days, emp, start, end)
        if reason_code not in CODES_REQUIRING_LWD:
            lwd = None

        data.append({
            "pf_number": emp.custom_esi_no,
            "employee_name": emp.employee_name,
            "paid_days": paid_days,
            "monthly_wages": wages,
            "reason_code": reason_code,
            "reason_description": REASON_CODES.get(reason_code),
            "last_working_day": lwd,
        })
    return data
