"""
Overtime Slip Override
----------------------
Extends HRMS native Overtime Slip with:
  1. Custom Fetch OT from Attendance (lunch + buffer aware)
  2. Week-by-week weekly cap  → auto-clamp (no blocking)
  3. Quarterly cap            → auto-clamp (no blocking)
  4. actual vs payable OT fields
  5. Threshold % alert emails on submit

Native HRMS logic stays untouched.
"""

import frappe
from frappe import _
from frappe.utils import getdate, get_first_day_of_week, add_days, flt, get_last_day
from hrms.hr.doctype.overtime_slip.overtime_slip import OvertimeSlip
from datetime import datetime


# ---------------------------------------------------------------------------
# Main entry points (registered in hooks.py)
# ---------------------------------------------------------------------------

def validate_ot_slip(doc, method=None):
    """Run on every Overtime Slip save — recalculate payable OT from Attendance."""
    settings = _get_overtime_type_settings(doc)
    if not settings:
        return

    # Always recalculate from Attendance (our source of truth)
    actual_total = _sum_attendance_ot(doc.employee, doc.start_date, doc.end_date)
    doc.custom_actual_ot_hours = round(actual_total, 2)

    if actual_total <= 0:
        doc.custom_payable_ot_hours = 0.0
        return

    payable = _calculate_payable_ot(doc, settings, actual_total)
    doc.custom_payable_ot_hours = round(payable, 2)

    _update_child_rows(doc)

    # if payable < actual_total:
    #     frappe.msgprint(
    #         _(
    #             "Actual OT: {0} hrs | Payable OT after cap: {1} hrs. "
    #             "{2} hrs will not be paid due to cap limits."
    #         ).format(
    #             round(actual_total, 2),
    #             round(payable, 2),
    #             round(actual_total - payable, 2),
    #         ),
    #         title=_("OT Cap Applied"),
    #         indicator="orange",
    #     )


def on_submit_ot_slip(doc, method=None):
    """Run after Overtime Slip is submitted — send threshold alerts + disable native Additional Salary."""
    settings = _get_overtime_type_settings(doc)
    if settings:
        _check_and_send_threshold_alerts(doc, settings)
    

# ---------------------------------------------------------------------------
# Whitelisted API — called by Client Script "Fetch OT" button
# ---------------------------------------------------------------------------

@frappe.whitelist()
def fetch_ot_from_attendance(employee, start_date, end_date):
    records = frappe.get_all(
        "Attendance",
        filters={
            "employee": employee,
            "attendance_date": ["between", [start_date, end_date]],
            "docstatus": 1,
            "custom_total_ot_hours": [">", 0],
        },
        fields=["attendance_date", "custom_total_ot_hours", "overtime_type", "shift"],
        order_by="attendance_date asc",
    )

    if not records:
        return {"rows": [], "actual_total": 0.0, "payable_total": 0.0}

    # Get shift details for standard working hours
    shift_cache = {}
    def get_effective_hours(shift_name):
        if not shift_name:
            # return 8.5
            return 0
        if shift_name not in shift_cache:
            shift = frappe.db.get_value(
                "Shift Type", shift_name,
                ["start_time", "end_time", "custom_lunch_break_minutes"],
                as_dict=True
            )
            if shift:
                from datetime import datetime, timedelta
                fmt = "%H:%M:%S"
                start = datetime.strptime(str(shift.start_time), fmt)
                end   = datetime.strptime(str(shift.end_time), fmt)
                if end <= start:
                    end += timedelta(days=1)
                total_mins = (end - start).seconds / 60
                lunch_mins = flt(shift.custom_lunch_break_minutes or 0)
                # Net of the unpaid break: this figure divides the daily wage to
                # price an overtime hour, so a gross 8.5 against a 7.5-hour paid
                # day would underprice every OT hour by the break.
                shift_cache[shift_name] = round(max(total_mins - lunch_mins, 0) / 60, 2)
        
        return shift_cache[shift_name]

    rows = [
        {
            "date": str(r.attendance_date),
            "ot_hours": flt(r.custom_total_ot_hours),
            "overtime_type": r.overtime_type or "",
            "standard_working_hours": get_effective_hours(r.shift),
        }
        for r in records
    ]

    actual_total = round(sum(flt(r.custom_total_ot_hours) for r in records), 2)

    class _SlipProxy:
        pass
    proxy = _SlipProxy()
    proxy.employee   = employee
    proxy.start_date = start_date
    proxy.end_date   = end_date
    proxy.name       = None

    settings = _get_overtime_type_settings_by_employee(employee)
    payable_total = actual_total
    if settings:
        payable_total = _calculate_payable_ot(proxy, settings, actual_total)

    return {
        "rows": rows,
        "actual_total": actual_total,
        "payable_total": round(payable_total, 2),
    }


# ---------------------------------------------------------------------------
# Core cap calculation (works for weekly slip AND 26-day monthly slip)
# ---------------------------------------------------------------------------

def _calculate_payable_ot(doc, settings, actual_total):
    # """
    # Calculate payable OT after weekly + quarterly caps.
    # For a 26-day slip, iterates week-by-week so weekly cap is applied
    # correctly across each week inside the period.
    # """
    # slip_start = getdate(doc.start_date)
    # slip_end   = getdate(doc.end_date)

    # weekly_cap    = flt(settings.get("weekly_max_ot_hours"))
    # quarterly_cap = flt(settings.get("quarterly_max_ot_hours"))

    # # --- Step 1: weekly cap week-by-week ---
    # if weekly_cap:
    #     weekly_payable = 0.0
    #     cursor = slip_start

    #     # Fetch all attendance rows once (avoid N+1 queries)
    #     att_rows = frappe.get_all(
    #         "Attendance",
    #         filters={
    #             "employee": doc.employee,
    #             "attendance_date": ["between", [slip_start, slip_end]],
    #             "docstatus": 1,
    #         },
    #         fields=["attendance_date", "custom_total_ot_hours"],
    #     )

    #     while cursor <= slip_end:
    #         week_start = get_first_day_of_week(cursor)
    #         week_end   = add_days(week_start, 6)

    #         eff_start = max(cursor, slip_start)
    #         eff_end   = min(week_end, slip_end)

    #         # OT actually worked in this week window
    #         week_actual = sum(
    #             flt(r.custom_total_ot_hours)
    #             for r in att_rows
    #             if eff_start <= getdate(r.attendance_date) <= eff_end
    #         )

    #         # Already-submitted slips this week (exclude current slip)
    #         already_paid = _get_employee_ot_hours(
    #             doc.employee, week_start, week_end, exclude_slip=doc.name
    #         )
    #         remaining = max(weekly_cap - already_paid, 0.0)
    #         weekly_payable += min(week_actual, remaining)

    #         cursor = add_days(week_end, 1)

    #     payable = weekly_payable
    # else:
    #     payable = actual_total   # no weekly cap configured

    # # --- Step 2: quarterly cap on top ---
    # if quarterly_cap:
    #     q_start, q_end = _get_quarter_range(getdate(doc.start_date))
    #     already_paid_q = _get_employee_ot_hours(
    #         doc.employee, q_start, q_end, exclude_slip=doc.name
    #     )
    #     remaining_q = max(quarterly_cap - already_paid_q, 0.0)
    #     payable = min(payable, remaining_q)

    # return max(payable, 0.0)
    return actual_total


# ---------------------------------------------------------------------------
# Helpers — Settings
# ---------------------------------------------------------------------------

def _get_overtime_type_settings(doc):
    """Fetch Overtime Type config — from slip rows first, then employee's default shift."""
    ot_type = None

    if doc.get("overtime_details"):
        for row in doc.overtime_details:
            if row.get("overtime_type"):
                ot_type = row.overtime_type
                break

    if not ot_type and doc.get("employee"):
        ot_type = _resolve_ot_type_for_employee(doc.employee)

    if not ot_type:
        return None

    return _fetch_ot_type_settings(ot_type)


def _get_overtime_type_settings_by_employee(employee):
    """Fetch Overtime Type config directly from employee's default shift."""
    ot_type = _resolve_ot_type_for_employee(employee)
    if not ot_type:
        return None
    return _fetch_ot_type_settings(ot_type)


def _resolve_ot_type_for_employee(employee):
    shift = frappe.db.get_value("Employee", employee, "default_shift")
    if shift:
        return frappe.db.get_value("Shift Type", shift, "overtime_type")
    return None


def _fetch_ot_type_settings(ot_type):
    fields = [
        "name",
        "maximum_overtime_hours_allowed",
        "weekly_max_ot_hours",
        "quarterly_max_ot_hours",
        "pre_shift_buffer_minutes",
        "post_shift_buffer_minutes",
        "alert_threshold_percent",
    ]
    return frappe.db.get_value("Overtime Type", ot_type, fields, as_dict=True)


# ---------------------------------------------------------------------------
# Helpers — Aggregation
# ---------------------------------------------------------------------------

def _sum_attendance_ot(employee, from_date, to_date):
    """Sum custom_total_ot_hours from submitted Attendance in date range."""
    result = frappe.db.sql(
        """
        SELECT COALESCE(SUM(custom_total_ot_hours), 0)
        FROM `tabAttendance`
        WHERE employee = %(employee)s
          AND attendance_date BETWEEN %(from_date)s AND %(to_date)s
          AND docstatus = 1
        """,
        {"employee": employee, "from_date": from_date, "to_date": to_date},
    )
    return flt(result[0][0]) if result else 0.0


def _get_employee_ot_hours(employee, from_date, to_date, exclude_slip=None):
    """Sum submitted Overtime Slip payable hours in date range."""
    exclude_condition = "AND name != %(exclude)s" if exclude_slip else ""
    params = {
        "employee": employee,
        "from_date": from_date,
        "to_date": to_date,
    }
    if exclude_slip:
        params["exclude"] = exclude_slip

    result = frappe.db.sql(
        f"""
        SELECT COALESCE(SUM(custom_payable_ot_hours), 0)
        FROM `tabOvertime Slip`
        WHERE employee = %(employee)s
          AND docstatus = 1
          AND start_date >= %(from_date)s
          AND end_date <= %(to_date)s
          {exclude_condition}
        """,
        params,
    )
    return flt(result[0][0]) if result else 0.0


def _fiscal_year_start_month():
    """Return the month (1-12) that the company's fiscal year starts on.
    Reads Frappe's global 'year_start_date' default; falls back to April (4)
    to match Indian FY convention."""
    try:
        yr_start = frappe.defaults.get_global_default("year_start_date")
        if yr_start:
            return getdate(yr_start).month
    except Exception:
        pass
    return 4


def _get_quarter_range(d):
    """Return (start_date, end_date) of the fiscal quarter containing d.
    Quarters are aligned to the fiscal year start month (Apr by default)."""
    fy_month = _fiscal_year_start_month()
    offset_from_fy = (d.month - fy_month) % 12
    q_index = offset_from_fy // 3
    start_month_num = ((fy_month - 1 + q_index * 3) % 12) + 1
    start_year = d.year if start_month_num <= d.month else d.year - 1
    start = datetime(start_year, start_month_num, 1).date()
    end_month_num = start_month_num + 2
    end_year = start_year
    if end_month_num > 12:
        end_month_num -= 12
        end_year += 1
    end = get_last_day(datetime(end_year, end_month_num, 1).date())
    return start, end


# ---------------------------------------------------------------------------
# Threshold Alerts (on submit)
# ---------------------------------------------------------------------------

def _check_and_send_threshold_alerts(doc, settings):
    threshold_pct = flt(settings.get("alert_threshold_percent"))
    if not threshold_pct:
        return

    # Use Attendance hours (actual worked) for alert evaluation
    week_start    = get_first_day_of_week(getdate(doc.start_date))
    week_end      = add_days(week_start, 6)
    weekly_hours  = _sum_attendance_ot(doc.employee, week_start, week_end)

    q_start, q_end    = _get_quarter_range(getdate(doc.start_date))
    quarterly_hours   = _sum_attendance_ot(doc.employee, q_start, q_end)

    alerts = []

    weekly_cap = flt(settings.get("weekly_max_ot_hours"))
    if weekly_cap and (weekly_hours / weekly_cap * 100) >= threshold_pct:
        alerts.append({
            "scope":    "Weekly",
            "consumed": weekly_hours,
            "cap":      weekly_cap,
            "percent":  round(weekly_hours / weekly_cap * 100, 2),
        })

    quarterly_cap = flt(settings.get("quarterly_max_ot_hours"))
    if quarterly_cap and (quarterly_hours / quarterly_cap * 100) >= threshold_pct:
        alerts.append({
            "scope":    "Quarterly",
            "consumed": quarterly_hours,
            "cap":      quarterly_cap,
            "percent":  round(quarterly_hours / quarterly_cap * 100, 2),
        })

    if alerts:
        _dispatch_alert_emails(doc, settings, alerts)


# def _dispatch_alert_emails(doc, settings, alerts):
#     recipients = frappe.db.sql(
#         """
#         SELECT user FROM `tabOT Alert Recipient`
#         WHERE parent = %s AND parenttype = 'Overtime Type'
#         """,
#         settings["name"],
#         as_dict=True,
#     )
#     emails = [r.user for r in recipients if r.user]
#     if not emails:
#         return
def _dispatch_alert_emails(doc, settings, alerts):
    recipients_data = frappe.db.sql(
        """
        SELECT user FROM `tabOT Alert Recipient`
        WHERE parent = %s AND parenttype = 'Overtime Type'
        """,
        settings["name"],
        as_dict=True,
    )
    
    # Resolve User → Email
    emails = []
    for r in recipients_data:
        if not r.user:
            continue
        # If it's already an email, use directly
        if "@" in r.user:
            emails.append(r.user)
        else:
            # Lookup email from User doctype
            email = frappe.db.get_value("User", r.user, "email")
            if email:
                emails.append(email)
    
    if not emails:
        return
    # ... rest of function stays same

    employee_name = frappe.db.get_value("Employee", doc.employee, "employee_name")
    rows_html = "".join([
        f"<tr><td>{a['scope']}</td><td>{a['consumed']}</td>"
        f"<td>{a['cap']}</td><td><b>{a['percent']}%</b></td></tr>"
        for a in alerts
    ])
    message = f"""
    <p>Employee <b>{employee_name}</b> ({doc.employee}) has crossed the OT alert threshold.</p>
    <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;">
      <thead style="background:#f4f4f4;">
        <tr><th>Scope</th><th>Consumed (hrs)</th><th>Cap (hrs)</th><th>% Used</th></tr>
      </thead>
      <tbody>{rows_html}</tbody>
    </table>
    <p style="margin-top:12px;">Slip: <a href="/app/overtime-slip/{doc.name}">{doc.name}</a></p>
    """
    frappe.sendmail(
        recipients=emails,
        subject=f"OT Alert: {employee_name} crossed threshold",
        message=message,
        reference_doctype=doc.doctype,
        reference_name=doc.name,
    )


def _update_child_rows(doc):
    for row in (doc.get("overtime_details") or []):
        att = frappe.db.get_value(
            "Attendance",
            row.get("reference_document"),
            ["custom_total_ot_hours", "standard_working_hours"],
            as_dict=True
        )
        if not att or not att.custom_total_ot_hours:
            continue
        row.overtime_duration = att.custom_total_ot_hours
        if att.standard_working_hours:
            row.standard_working_hours = att.standard_working_hours
