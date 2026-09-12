import frappe
from frappe.utils import flt

# Attendance statuses that can carry payable hours. 'On Leave' is included
# because a Working Hours shift credits paid leave the full shift duration
# (LWP resolves to 0 in the base pass, so summing is safe either way).
# 'Absent' stays out — it can never carry hours.
PAYABLE_ATTENDANCE_STATUSES = ("Present", "Half Day", "Work From Home", "On Leave")

# 'Absent' is not in that tuple, but an Absent row still reaches the sum when it
# carries payable hours — see the query below and
# Shift Type.custom_pay_hours_when_absent.


def fetch_working_hours(doc, method=None):
    """Aggregate the period's payable Attendance hours onto the Salary Slip.

    custom_regular_working_hours is SUMmed from Attendance.custom_base_hours —
    it is deliberately NOT computed as (working_hours - OT hours). The daily OT
    cap in attendance.apply_buffer_logic trims hours off custom_total_ot_hours
    without removing them from working_hours, so the subtraction would hand
    capped-off OT back to the employee at the basic rate and defeat the cap.

    No Shift Type join is needed to keep 'Present / Absent' basis days out of
    the sum: the base pass in attendance.py already forces custom_base_hours to
    0 for those days, and it resolves the shift through fallbacks (Shift
    Assignment, Employee.default_shift) that a plain SQL join on
    Attendance.shift would miss. custom_base_hours is the authoritative,
    already-basis-aware value. Legacy rows written before the payroll-basis
    field existed are corrected by patches/reset_base_hours_by_payroll_basis.py.

    Must run before SalarySlip.validate() (which calls calculate_net_pay), or
    salary component formulas read the previous save's values. Registered on
    before_validate in hooks.py for that reason.
    """
    if not doc.employee or not doc.start_date or not doc.end_date:
        return

    row = frappe.db.sql("""
        SELECT
            COALESCE(SUM(a.working_hours), 0)          AS total_hours,
            COALESCE(SUM(a.custom_base_hours), 0)      AS base_hours,
            COALESCE(SUM(a.custom_total_ot_hours), 0)  AS ot_hours
        FROM `tabAttendance` a
        WHERE a.employee = %(employee)s
          AND a.docstatus = 1
          AND a.attendance_date BETWEEN %(start_date)s AND %(end_date)s
          AND (
                a.status IN %(statuses)s
                -- Absent days carry hours only where the Shift Type opts in via
                -- custom_pay_hours_when_absent; the base pass has already
                -- written 0 everywhere else, so testing the hours rather than
                -- the shift keeps this one query free of a Shift Type join and
                -- of the fallback chain that resolves a row's real shift.
                OR (a.status = 'Absent' AND a.custom_base_hours > 0)
              )
    """, {
        "employee": doc.employee,
        "start_date": doc.start_date,
        "end_date": doc.end_date,
        "statuses": PAYABLE_ATTENDANCE_STATUSES,
    }, as_dict=True)

    result = row[0] if row else {}
    doc.custom_regular_working_hours = flt(result.get("base_hours"), 2)

    # PAID hours, not time on site: regular + overtime, so the three figures on
    # the slip always add up. The raw clock sum does not, and the difference is
    # invisible to anyone reading the payslip — on a shift with
    # custom_cap_base_at_shift_hours the hours above a full day that fell inside
    # the OT buffer are paid at neither rate, so a day worked 9.27 shows base
    # 8.00 + OT 1.06 and 0.21 hours simply vanish from the arithmetic. Reading
    # 109.47 against 93.39 + 15.36 = 108.75 looks like a bug in the payslip.
    # The raw figure is still on every Attendance row (working_hours) for anyone
    # auditing time on site.
    #
    # Overtime Slips are the source when they exist, because that is what the OT
    # salary component is paid from (fetch_ot_hours runs first — see hooks.py
    # and salary_slip_class). Attendance OT is the fallback for a period whose
    # Overtime Slips have not been raised yet, so the total is never short.
    ot_hours = flt(doc.get("custom_actual_ot_hours")) or flt(result.get("ot_hours"))
    doc.custom_total_working_hours = flt(doc.custom_regular_working_hours + ot_hours, 2)
    doc.custom_standard_day_hours = _standard_day_hours(doc)
    doc.custom_holiday_hours = _holiday_hours(doc, doc.custom_standard_day_hours)
    doc.custom_standard_month_hours = _standard_month_hours(doc, doc.custom_standard_day_hours)
    doc.custom_payroll_basis = payroll_basis_for(_resolve_period_shift(doc))
    # Hours have just been rebuilt from Attendance, so any Sunday deduction
    # applied earlier in a previous cycle is gone. Clear the guard so
    # apply_sunday_deduction runs exactly once against these fresh values.
    doc.flags.sunday_deduction_applied = False


# Used when no shift can be resolved for the period. Matches the divisor the OT
# component used before this field existed, so an unresolvable employee keeps
# their previous rate instead of a ZeroDivisionError inside a salary formula.
DEFAULT_STANDARD_DAY_HOURS = 8.0


def payroll_basis_for(shift_name):
    """The payroll basis a shift pays on, defaulting to Present / Absent.

    Shared by the Salary Slip hook and the Salary Structure Assignment eval
    context so both agree on which branch an employee belongs to. Basis is a
    property of the shift, not of the payroll period, which is what lets a
    salary component CONDITION on it — the assignment pre-filters components
    with no period available, so anything period-dependent (like hours) cannot
    be used there.
    """
    from frontier_hr.overtime_extension.overrides.attendance import (
        PAYROLL_BASIS_ATTENDANCE,
    )

    if not shift_name:
        return PAYROLL_BASIS_ATTENDANCE
    return (
        frappe.db.get_value("Shift Type", shift_name, "custom_payroll_basis")
        or PAYROLL_BASIS_ATTENDANCE
    )


def shift_on_date(employee, as_of_date):
    """The employee's shift on a single date — no payroll period involved.

    Used by the Salary Structure Assignment, which has an employee and a
    from_date but no slip. Mirrors the tail of _resolve_period_shift.

    Resolution order, strongest evidence first:

      1. a submitted, Active Shift Assignment covering the date
      2. Employee.default_shift
      3. the employee's nearest Shift Assignment, whatever its dates

    Step 3 exists because the failure is SILENT and expensive. The SSA
    pre-evaluates every salary component and DROPS the rows whose condition is
    falsey, so an employee with no resolvable shift takes the
    'Present / Absent' default, the hours-based Basic fails its condition, and
    the row never reaches the slip. The slip then drops the payment-days Basic
    too — its own basis IS 'Working Hours' — and the employee is issued a
    payslip with no basic pay at all, plus a 0 for every component keyed on it
    (PF, ESI). No error is raised anywhere.

    That is reachable on any site: a Salary Structure Assignment dated before
    the employee's first Shift Assignment is enough — an assignment created
    later, or backdated payroll, and nothing covers the SSA's from_date. A
    shift assigned on some other date is far better evidence of how this
    employee is paid than no shift at all, so it is used rather than falling
    through to a default that silently deletes their basic pay.
    """
    if not employee:
        return None

    assignment = frappe.get_all(
        "Shift Assignment",
        filters={
            "employee": employee,
            "docstatus": 1,
            "status": "Active",
            "start_date": ["<=", as_of_date],
        },
        or_filters=[["end_date", ">=", as_of_date], ["end_date", "is", "not set"]],
        pluck="shift_type",
        order_by="start_date desc",
        limit=1,
    )
    if assignment:
        return assignment[0]

    default_shift = frappe.db.get_value("Employee", employee, "default_shift")
    if default_shift:
        return default_shift

    nearest = frappe.get_all(
        "Shift Assignment",
        filters={"employee": employee, "docstatus": 1, "status": "Active"},
        pluck="shift_type",
        order_by="start_date asc",
        limit=1,
    )
    return nearest[0] if nearest else None


def _standard_day_hours(doc):
    """Hours in one full working day on this employee's shift for the period.

    Divisor for the hourly rate (base / 26 / this), so it must be the shift's
    own full-day figure — 8.5 for an 09:00-17:30 shift, 8 for a 22:00-06:00
    Night Shift. Hard-coding one number silently misprices every other shift.

    Reuses attendance._credited_day_hours so a shift that excludes breaks from
    working_hours is divided by the same net figure its Attendance rows are
    measured in — otherwise a full month of perfect attendance would not add
    back up to a full month's base.
    """
    from frontier_hr.overtime_extension.overrides.attendance import _credited_day_hours

    shift_name = _resolve_period_shift(doc)
    if not shift_name:
        return DEFAULT_STANDARD_DAY_HOURS

    shift = frappe.db.get_value(
        "Shift Type", shift_name,
        ["start_time", "end_time", "custom_lunch_break_minutes", "working_hours_calculation_based_on"],
        as_dict=True,
    )
    if not shift:
        return DEFAULT_STANDARD_DAY_HOURS

    return flt(_credited_day_hours(shift), 2) or DEFAULT_STANDARD_DAY_HOURS


# Working days in a nominal month, used only when an employee has no Holiday
# List to count real holidays from. Matches the /26 convention the OT component
# still uses.
DEFAULT_WORKING_DAYS = 26


def _holiday_list_for(employee):
    """The employee's Holiday List, falling back to the company default."""
    holiday_list, company = frappe.db.get_value("Employee", employee, ["holiday_list", "company"])
    if not holiday_list:
        holiday_list = frappe.db.get_value("Company", company, "default_holiday_list")
    return holiday_list


def _holiday_hours(doc, day_hours):
    """Paid holiday hours for the period: Holiday List days x a full shift day.

    Holidays are paid days. Crediting them explicitly - rather than leaving them
    implicit in an inflated hourly rate - does three things:

      1. The payslip SHOWS the holiday pay, instead of it hiding inside the
         divisor. That is what an approver needs to see.
      2. It gives the Sunday Deduction Policy something correct to take from.
         Deducting a Sunday now literally removes that Sunday's hours from the
         paid-holiday pool. Previously it came off worked hours, and could
         destroy wages the employee had actually earned - measured on
         recode.com as six employees paid 0.00 despite working, e.g. 108-U2-0201
         earned 3.14 h and lost all of it to a 25.5 h deduction.
      3. It makes a Working Hours employee with no attendance land on exactly
         the same figure as native Present / Absent, which also pays holidays
         regardless of attendance (13,486 x 6/31 = 2,610.19 either way).

    Worked hours are protected by construction, not by a cap: every deductible
    Sunday is itself a Holiday List day, and the weekly rule can take at most
    one Sunday per week, so the deduction can never exceed this credit.

    EMPLOYMENT WINDOW: holidays are counted only for the part of the period the
    employee was actually employed — from date_of_joining, to relieving_date.
    A holiday outside that window was never theirs to be paid for. Without the
    clamp, someone relieved on 3 August was still credited every Sunday and
    festival for the rest of the month, and someone joining on the 10th was
    paid for the two Sundays before they arrived. Neither shows up as an
    absence, so no Sunday-deduction rule can catch it: there is nothing to
    deduct FROM an employment that had not started or had already ended.

    Native Present / Absent payroll gets this right on its own — hrms prorates
    payment_days by joining and relieving dates. This is the hours-based
    equivalent.
    """
    day_hours = flt(day_hours) or DEFAULT_STANDARD_DAY_HOURS
    holiday_list = _holiday_list_for(doc.employee)
    if not holiday_list:
        return 0.0

    from_date, to_date = _employment_window(doc)
    if not from_date or not to_date or to_date < from_date:
        return 0.0

    holidays = frappe.db.count(
        "Holiday",
        {"parent": holiday_list, "holiday_date": ["between", [from_date, to_date]]},
    )
    return flt(holidays * day_hours, 2)


def _employment_window(doc):
    """The slip period clipped to the employee's actual employment dates.

    Returns (from_date, to_date), either of which is None when the employee was
    not employed at any point in the period.
    """
    from frappe.utils import getdate

    joining, relieving = frappe.db.get_value(
        "Employee", doc.employee, ["date_of_joining", "relieving_date"]
    ) or (None, None)

    from_date = getdate(doc.start_date)
    to_date = getdate(doc.end_date)

    if joining and getdate(joining) > from_date:
        from_date = getdate(joining)
    if relieving and getdate(relieving) < to_date:
        to_date = getdate(relieving)

    return from_date, to_date


def _standard_month_hours(doc, day_hours):
    """Hours in a FULL month on this shift: CALENDAR days x standard day hours.

    Calendar days, not working days, because holidays are now paid explicitly
    through custom_holiday_hours. Worked hours plus holiday hours add up to the
    whole month, so the divisor has to span the whole month too:

        Aug  212.5 worked + 51.0 holiday = 263.5 = 31 x 8.5   -> full base
        Nov  195.5 worked + 59.5 holiday = 255.0 = 30 x 8.5   -> full base

    Full attendance therefore pays exactly base in every month, and the rate
    moves less than it did under the working-days divisor (10.7% spread across
    2026 rather than 17.4%), because only the length of the month varies rather
    than the holiday count as well.

    The OT component deliberately does NOT use this - overtime stays on the flat
    /26 rate so a given overtime hour is worth the same all year.
    """
    from frappe.utils import date_diff, getdate

    day_hours = flt(day_hours) or DEFAULT_STANDARD_DAY_HOURS
    days = date_diff(getdate(doc.end_date), getdate(doc.start_date)) + 1

    return flt(days * day_hours, 2) or (DEFAULT_WORKING_DAYS * day_hours)


def fetch_ot_hours(doc, method=None):
    if not doc.employee or not doc.start_date or not doc.end_date:
        return

    # Get ACTUAL OT hours (not capped) from Overtime Slips
    result = frappe.db.sql("""
        SELECT COALESCE(SUM(custom_actual_ot_hours), 0)
        FROM `tabOvertime Slip`
        WHERE employee = %(employee)s
          AND docstatus = 1
          AND start_date >= %(start_date)s
          AND end_date <= %(end_date)s
    """, {
        "employee": doc.employee,
        "start_date": doc.start_date,
        "end_date": doc.end_date
    })
    doc.custom_actual_ot_hours = flt(result[0][0]) if result else 0.0

    # Standard multiplier
    multiplier = 1.0
    shift = _resolve_period_shift(doc)
    if shift:
        ot_type = frappe.db.get_value("Shift Type", shift, "overtime_type")
        if ot_type:
            mult_value = frappe.db.get_value("Overtime Type", ot_type, "standard_multiplier")
            multiplier = flt(mult_value) or 2.0
    doc.custom_standard_multiplier = multiplier


def _resolve_period_shift(doc):
    """The Shift Type this employee actually worked during the slip period.

    Reading Employee.default_shift alone is not enough — on a site that drives
    shifts through Shift Assignment that field is usually blank, and every such
    employee silently falls back to a multiplier of 1.0 no matter what their
    Overtime Type says. Resolution order, widest evidence first:

      1. the shift stamped on their Attendance in this period (most frequent),
         which is the shift that actually earned the overtime
      2. a submitted, Active Shift Assignment overlapping the period
      3. Employee.default_shift
    """
    row = frappe.db.sql("""
        SELECT a.shift
        FROM `tabAttendance` a
        WHERE a.employee = %(employee)s
          AND a.docstatus = 1
          AND a.shift IS NOT NULL AND a.shift != ''
          AND a.attendance_date BETWEEN %(start_date)s AND %(end_date)s
        GROUP BY a.shift
        ORDER BY COUNT(*) DESC
        LIMIT 1
    """, {"employee": doc.employee, "start_date": doc.start_date, "end_date": doc.end_date})
    if row:
        return row[0][0]

    assignment = frappe.get_all(
        "Shift Assignment",
        filters={
            "employee": doc.employee,
            "docstatus": 1,
            "status": "Active",
            "start_date": ["<=", doc.end_date],
        },
        or_filters=[["end_date", ">=", doc.start_date], ["end_date", "is", "not set"]],
        pluck="shift_type",
        order_by="start_date desc",
        limit=1,
    )
    if assignment:
        return assignment[0]

    default_shift = frappe.db.get_value("Employee", doc.employee, "default_shift")
    if default_shift:
        return default_shift

    # Same last resort as shift_on_date — see its docstring. A period with no
    # attendance and no overlapping assignment would otherwise resolve to no
    # basis at all and strip the employee's basic pay off the slip.
    nearest = frappe.get_all(
        "Shift Assignment",
        filters={"employee": doc.employee, "docstatus": 1, "status": "Active"},
        pluck="shift_type",
        order_by="start_date asc",
        limit=1,
    )
    return nearest[0] if nearest else None
    