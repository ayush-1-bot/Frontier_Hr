"""
Attendance Base Hours + Buffer Logic
------------------------------------
Two independent passes run on every Attendance save/submit:

  BASE PASS   (_compute_base_hours) — runs for EVERY shift.
      Driven by `Shift Type.custom_payroll_basis`:

        "Present / Absent"  (default)
            Native payroll-day behaviour. `custom_base_hours` is forced to 0
            so nothing this app writes ever reaches the Salary Slip's
            custom_regular_working_hours. Without this the day would be paid
            twice — once through native payment_days, once through an
            hours-based salary component formula.

        "Working Hours"
            Base pay is measured in hours, sourced from native
            Attendance.working_hours (see _worked_hours):
              punched day        -> worked - uncapped OT, uncapped otherwise
              paid leave / WFH   -> a full credited day (see _credited_day_hours)
              LWP                -> 0
              Half Day, other half paid leave -> worked + half a credited day

EFFECTIVE HOURS
    Every hour figure this module writes — base hours, credited leave/WFH days,
    the threshold excess, the window-model eligibility bar — is EFFECTIVE: net
    of the Shift Type's unpaid lunch break (`custom_lunch_break_minutes`). Pay
    is never based on the full shift duration. `_worked_hours` remains the raw
    measurement of time on site; `_effective_worked_hours` is what the day is
    worth, and it is the only one the pay passes consult. The one shift setting
    that changes this is "Every Valid Check-in and Check-out", where hrms has
    already removed the punch-out gap from working_hours and a second deduction
    would double-count — see _effective_worked_hours.

  OT PASS     (_compute_overtime) — runs only when the Shift Type has
      `allow_overtime` on and an `overtime_type` set. Independent of the
      payroll basis: a shift can earn OT while still being paid on
      present/absent days, and a Working Hours shift can have OT disabled.

      THRESHOLD MODEL  (Shift Type.custom_ot_qualifying_hours > 0)
          Overtime is the excess over N hours worked, so hours outside the shift
          window count toward N exactly like hours inside it — a late arrival is
          made up before any overtime is earned. N must be set to whatever a
          full normal day yields as EFFECTIVE hours on that shift — native
          Attendance.working_hours after the unpaid lunch break comes off.

          The shift buffers STILL APPLY (unless custom_apply_ot_buffers is off):
          the excess is capped by whichever ends of the day actually sit outside
          their buffer. Overtime is min(excess, qualifying early + late). The
          excess stops a late arrival claiming the whole after-shift period; the
          buffers stop a six-minute overrun becoming paid overtime.

      WINDOW MODEL     (custom_ot_qualifying_hours = 0, legacy behaviour)
          1. Pre/Post Shift Buffer (grace period)
          2. Lunch Break Duration deducted from shift hours to set the
             eligibility bar (effective hours worked must clear it)
          3. Status + working hours validation (no OT for absent/half-worked staff)

      custom_apply_ot_buffers (default on) switches the grace period off in
      either model: any time outside the window, or past N, then counts.

Both OT models then apply the daily OT cap
(Overtime Type.maximum_overtime_hours_allowed).

`custom_base_hours` is written by the base pass and is the authoritative
payable base for the day. Salary Slip SUMs it — it must never be re-derived as
`working_hours - custom_total_ot_hours`, because the daily cap breaks that
identity (capped-off hours would silently return as base-rate pay).
"""

import frappe
from frappe.utils import get_datetime, flt, getdate, cint
from datetime import timedelta

PAYROLL_BASIS_ATTENDANCE = "Present / Absent"
PAYROLL_BASIS_HOURS = "Working Hours"

SHIFT_FIELDS = [
    "start_time", "end_time", "overtime_type", "allow_overtime",
    "custom_lunch_break_minutes", "custom_ot_qualifying_hours",
    "custom_payroll_basis", "working_hours_calculation_based_on",
    "custom_apply_ot_buffers", "custom_cap_base_at_shift_hours",
    "custom_pay_hours_when_absent",
]

# Native Shift Type option that makes Attendance.working_hours exclude the gaps
# between punch pairs (a lunch punch-out/in), rather than measuring first-in to
# last-out.
EXCLUDES_BREAKS = "Every Valid Check-in and Check-out"


def apply_buffer_logic(doc, method=None):
    shift = _get_shift_settings(doc)
    # Native working_hours is rewritten to the EFFECTIVE figure first, so the
    # field on the form, every native report and the Salary Slip's
    # custom_total_working_hours all read the same hours the pay is built from.
    _override_native_working_hours(doc, shift)
    _override_native_standard_hours(doc, shift)
    # OT first: the base pass subtracts the UNCAPPED OT hours so that
    # base + OT can never exceed the hours actually worked. The window model
    # derives OT from clock position rather than from an excess over N, so
    # without this the after-shift hours would be counted twice — once as OT,
    # once inside base — on any day with a late arrival AND a late departure.
    uncapped_ot = _compute_overtime(doc, shift)
    _compute_base_hours(doc, shift, uncapped_ot)


# ---------------------------------------------------------------------------
# Shift resolution
# ---------------------------------------------------------------------------

def _resolve_shift_name(doc):
    """Attendance.shift, falling back to an active submitted Shift Assignment
    covering the date, then Employee.default_shift.

    The fallbacks matter for days with no punches (leave, WFH) — hrms does not
    always stamp a shift on those Attendance rows, and without a shift there is
    no basis and no duration to credit.
    """
    if doc.get("shift"):
        return doc.shift

    if not doc.get("employee"):
        return None

    attendance_date = getdate(doc.get("attendance_date")) if doc.get("attendance_date") else None
    if attendance_date:
        assignment = frappe.get_all(
            "Shift Assignment",
            filters={
                "employee": doc.employee,
                "docstatus": 1,
                "status": "Active",
                "start_date": ["<=", attendance_date],
            },
            or_filters=[
                ["end_date", ">=", attendance_date],
                ["end_date", "is", "not set"],
            ],
            pluck="shift_type",
            order_by="start_date desc",
            limit=1,
        )
        if assignment:
            return assignment[0]

    return frappe.db.get_value("Employee", doc.employee, "default_shift")


def _get_shift_settings(doc):
    shift_name = _resolve_shift_name(doc)
    if not shift_name:
        return None
    shift = frappe.db.get_value("Shift Type", shift_name, SHIFT_FIELDS, as_dict=True)
    if shift:
        shift.name = shift_name
    return shift


def _shift_duration_hours(shift):
    """Full elapsed shift duration in hours, start_time to end_time."""
    if not shift or not shift.get("start_time") or not shift.get("end_time"):
        return 0.0

    ref = "2000-01-01"
    start_dt = get_datetime(f"{ref} {shift.start_time}")
    end_dt = get_datetime(f"{ref} {shift.end_time}")
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)
    return (end_dt - start_dt).total_seconds() / 3600.0


def _worked_hours(doc):
    """Hours actually worked on this Attendance row.

    Native `working_hours` is authoritative: hrms computes it in
    employee_checkin.calculate_working_hours() honouring the Shift Type's own
    `working_hours_calculation_based_on`. Under "Every Valid Check-in and
    Check-out" it EXCLUDES the gaps between punch pairs, so an employee who
    punches out for lunch has working_hours below their first-in/last-out
    elapsed time. Measuring elapsed here instead would over-count base hours by
    the break on every such shift, and would break outright on days with
    multiple check-in/check-out pairs.

    Elapsed between in_time and out_time is only a fallback for rows where
    working_hours was never populated.
    """
    worked = flt(doc.get("working_hours") or 0)
    if worked > 0:
        return worked

    if doc.get("in_time") and doc.get("out_time"):
        elapsed = (get_datetime(doc.out_time) - get_datetime(doc.in_time)).total_seconds() / 3600.0
        return max(elapsed, 0.0)

    return 0.0


def _lunch_hours(shift):
    """The shift's unpaid lunch break, in hours. 0.0 when there is no shift.

    Floored at 0: a negative value typed into the field would otherwise ADD
    hours everywhere the break is subtracted, crediting a leave day more than
    the shift is long and inflating the eligibility bar.
    """
    if not shift:
        return 0.0
    return max(flt(shift.get("custom_lunch_break_minutes") or 0) / 60.0, 0.0)


def _effective_worked_hours(doc, shift):
    """Hours worked NET of the unpaid lunch break — the figure all pay derives
    from. `_worked_hours` stays the raw measurement of time on site; this is
    what the day is actually worth.

    DOUBLE-COUNT GUARD: when the Shift Type measures on "Every Valid Check-in
    and Check-out", hrms already drops the gaps between punch pairs from native
    working_hours, so an employee who punched out for lunch has had that break
    removed once already. Subtracting custom_lunch_break_minutes on top would
    deduct the same break twice and underpay the day by an hour, so the raw
    figure is returned unchanged on those shifts.

    Under every other measurement method working_hours spans first-in to
    last-out and still contains the break, so it is deducted here — ALWAYS, and
    floored at 0. Skipping the deduction on days shorter than the break itself
    reads as fairer, but it makes pay go DOWN as time on site goes up: against a
    60-minute break a 60-minute day was worth 1.00 h and a 61-minute day 0.02 h.
    A day that short is under the Absent threshold and earns nothing either way,
    so the cliff bought nothing and only broke the guarantee that working longer
    never pays less.
    """
    worked = _worked_hours(doc)

    if shift and shift.get("working_hours_calculation_based_on") == EXCLUDES_BREAKS:
        return worked

    if _override_applies(doc, shift):
        # _override_native_working_hours already wrote the effective figure into
        # the field, so deducting again would take the break off twice. Derived
        # from the punches rather than read back, for one reason: a day worth
        # exactly 0 effective hours stores working_hours = 0, and _worked_hours
        # reads a stored 0 as "never populated" and falls back to elapsed time.
        # Reading it back therefore handed a 60-minute day on a 60-minute break
        # a full hour of pay, while a 61-minute day got one minute.
        return _effective_from_punches(doc, shift)

    return max(worked - _lunch_hours(shift), 0.0)


def _override_applies(doc, shift):
    """Whether native working_hours on this row holds the effective figure
    because _override_native_working_hours rewrote it. Single source of truth
    for that condition — the override and the reader must never disagree, or
    the break is deducted twice or not at all."""
    return bool(
        shift
        and shift.get("working_hours_calculation_based_on") != EXCLUDES_BREAKS
        and doc.get("in_time")
        and doc.get("out_time")
    )


def _override_native_working_hours(doc, shift):
    """Rewrite native Attendance.working_hours to the effective figure.

    The field then means the same thing everywhere: hours the day is PAID for,
    net of the unpaid lunch break. Without this the form shows 8.50 while the
    slip prices 7.50 and nobody can reconcile the two.

    IDEMPOTENT, and that is the whole difficulty — the hook re-runs on every
    save, so it must never subtract the break from its own previous result. It
    does not read the stored value at all: under "First Check-in and Last
    Check-out" hrms defines working_hours as exactly last-out minus first-in,
    so the raw figure is recomputed from the punches every time and the write
    is a pure function of them.

    Runs even when the break is 0, which is what makes the setting REVERSIBLE:
    the write is (last-out - first-in - break), so clearing
    custom_lunch_break_minutes and recomputing restores the full clock hours.
    Were it skipped at 0, rows written while a break was configured would keep
    the deducted figure for ever, with nothing to put the hour back.

    Left untouched, on purpose:
      * shifts on "Every Valid Check-in and Check-out" — hrms already dropped
        the break there, so the stored value IS effective, and last-out minus
        first-in is not how that setting measures a day
      * rows with no punches (HR typed the hours by hand, leave, WFH) — there
        is nothing to recompute a raw figure from, so overwriting would
        compound on each save

    Status is unaffected: hrms picks Absent / Half Day from its own raw figure
    inside process_auto_attendance, before the Attendance row is ever inserted,
    so the thresholds keep measuring time on site rather than paid hours.
    """
    if not _override_applies(doc, shift):
        return

    doc.working_hours = round(_effective_from_punches(doc, shift), 4)


def _effective_from_punches(doc, shift):
    """Effective hours computed straight from the punches — last-out minus
    first-in, less the unpaid break. Exactly how hrms defines working_hours
    under "First Check-in and Last Check-out", which is what makes the override
    idempotent: it is a pure function of the punches, so re-running the hook on
    an already-overridden row reproduces the same number instead of deducting
    the break a second time."""
    elapsed = (
        get_datetime(doc.out_time) - get_datetime(doc.in_time)
    ).total_seconds() / 3600.0
    return max(elapsed - _lunch_hours(shift), 0.0)


def _override_native_standard_hours(doc, shift):
    """Rewrite native Attendance.standard_working_hours to the effective day.

    hrms sets it in employee_checkin.get_overtime_data as plain shift end minus
    start — 8.5 on an 09:00-17:30 shift, with no regard for the unpaid break —
    and shows it on the Attendance form right next to a working_hours that IS
    net of the break. The two then disagree on what a standard day is, and
    hrms's own Overtime Slip divides the daily wage by this figure to price an
    overtime hour, so leaving it gross underprices overtime by the break.

    Written on every row of a shift that allows overtime, not only the days that
    happened to run long as hrms does — it is a property of the shift, not an
    outcome of the day, and a stored 0 is a division hazard downstream.

    actual_overtime_duration is hrms's own formula (worked beyond a standard
    day) recomputed on effective inputs, so the two fields cannot contradict
    each other on the form. It is NOT what this app pays on: buffers, the
    qualifying threshold and the daily cap all live in custom_total_ot_hours.
    """
    if not shift or not shift.get("allow_overtime"):
        return

    standard = _credited_day_hours(shift)
    if not standard:
        return

    doc.standard_working_hours = round(standard, 4)
    doc.actual_overtime_duration = round(
        max(_effective_worked_hours(doc, shift) - standard, 0.0), 4
    )


def _payable_worked_hours(doc, shift, uncapped_ot):
    """Effective worked hours that belong to BASE rather than to overtime.

    Always `worked - uncapped_ot`, never `min(worked, N)`. The two agree under
    the threshold model (OT there is exactly the excess over N), but diverge
    under the window model, where OT is measured from shift end and a late
    arrival leaves worked hours below a full shift. Subtracting is the form
    that holds for both.

    The UNCAPPED OT is subtracted on purpose: hours the daily cap trims are
    paid at neither rate. Letting them fall back into base would return
    capped-off overtime at basic rate and defeat the cap.
    """
    return max(_effective_worked_hours(doc, shift) - flt(uncapped_ot), 0.0)


def _credited_day_hours(shift):
    """Hours credited for a day with no punches (paid leave, WFH) on a Working
    Hours shift — what a normally worked day on this shift yields.

    Shift duration minus the unpaid lunch break, ALWAYS. The break is unpaid
    however the shift measures its hours, so a standard day is worth the
    effective hours and never the full window: crediting the full duration
    would let a leave or WFH day out-earn the same day worked, which is exactly
    what an effective-hours worked figure now returns.
    """
    duration = _shift_duration_hours(shift) - _lunch_hours(shift)
    return max(duration, 0.0)


# ---------------------------------------------------------------------------
# Base pass — runs for every shift
# ---------------------------------------------------------------------------

def _is_paid_leave(doc):
    """On Leave against a Leave Type that is not LWP. Same is_lwp lookup
    sunday_deduction/rules.py::count_absent_equivalent uses."""
    if not doc.get("leave_type"):
        # On Leave with no leave type recorded — treat as paid, matching the
        # absent-equivalent rules which only count leave as absent when the
        # Leave Type is explicitly is_lwp.
        return True
    return not frappe.db.get_value("Leave Type", doc.leave_type, "is_lwp")


def _compute_base_hours(doc, shift, uncapped_ot=0.0):
    basis = (shift or {}).get("custom_payroll_basis") or PAYROLL_BASIS_ATTENDANCE

    if basis != PAYROLL_BASIS_HOURS:
        # Native present/absent day. Write no hours — see module docstring.
        doc.custom_base_hours = 0
        return

    credited_hours = _credited_day_hours(shift)
    status = doc.get("status")

    if status == "On Leave":
        base = credited_hours if _is_paid_leave(doc) else 0.0

    elif status == "Work From Home":
        # Credited in full regardless of punches — a WFH day is a worked day.
        base = credited_hours

    elif status == "Half Day" and doc.get("half_day_status") == "Present":
        # Other half covered by paid leave: worked hours plus the paid half,
        # never more than a full day — the leave half is a credit, not time
        # worked, so it cannot push the day past one full shift.
        base = min(
            _payable_worked_hours(doc, shift, uncapped_ot) + (credited_hours / 2.0),
            credited_hours,
        )

    elif status == "Absent":
        # An Absent day pays nothing by default: the status means the day did
        # not count, and hrms sets it from the shift's own
        # working_hours_threshold_for_absent.
        #
        # custom_pay_hours_when_absent reverses that for sites that pay strictly
        # for time on site. The employee WAS there — 08:58 to 10:12 is 1.22 h on
        # the clock, 0.72 h after the unpaid break — and on an hours-based
        # payroll refusing to pay it is a decision about the label, not about
        # the work. The status is left untouched either way, so the attendance
        # register still shows the day as Absent for leave and discipline
        # purposes; only the hours become payable.
        if cint(shift.get("custom_pay_hours_when_absent")):
            base = _payable_worked_hours(doc, shift, uncapped_ot)
            if cint(shift.get("custom_cap_base_at_shift_hours")) and credited_hours:
                base = min(base, credited_hours)
        else:
            base = 0.0

    else:
        # By default there is NO ceiling on a worked day. Time that fell short
        # of the OT buffer — e.g. leaving exactly 30 minutes late against a
        # 30-minute buffer — earns no overtime, but it was still worked, so it
        # stays in base rather than going unpaid.
        base = _payable_worked_hours(doc, shift, uncapped_ot)

        # custom_cap_base_at_shift_hours reverses that for shifts that would
        # rather a day never exceed one full shift: the buffer-absorbed minutes
        # are discarded instead of paid at normal rate. On an 8.5 h shift a
        # 09:00-17:36 day is then credited 8.50 rather than 8.60.
        if cint(shift.get("custom_cap_base_at_shift_hours")) and credited_hours:
            base = min(base, credited_hours)

    doc.custom_base_hours = round(max(base, 0.0), 4)


# ---------------------------------------------------------------------------
# OT pass — gated on allow_overtime, independent of payroll basis
# ---------------------------------------------------------------------------

def _clear_ot_fields(doc):
    """Zero every OT field. Called before any early return so a re-save cannot
    leave stale overtime behind.

    The base pass ALWAYS recomputes custom_base_hours from scratch, so an early
    return that left the old OT values in place produced a day paying more than
    it should: a 10 h day with 1.5 h OT, re-saved after out_time is cleared,
    became base 10.0 + OT 1.5 = 11.5 h. Turning off allow_overtime, clearing the
    Overtime Type, or a status change to Absent did the same.
    """
    doc.custom_early_ot_minutes = 0
    doc.custom_late_ot_minutes = 0
    doc.custom_total_ot_hours = 0
    doc.custom_ot_status = ""


def _compute_overtime(doc, shift):
    """Write the OT fields and return the UNCAPPED OT hours for the base pass."""
    _clear_ot_fields(doc)

    # overtime_type is native, and hrms hangs the visibility of its whole
    # Overtime section off it (`depends_on: overtime_type`) — as does our
    # Overtime Detail section. It therefore has to be stamped from the SHIFT,
    # not from the outcome of the day: stamping it only on days that earned
    # overtime made the section appear and vanish between one day and the next
    # on the same employee, and clearing it is what stops a shift that no
    # longer allows overtime leaving an empty section on screen for ever.
    if not shift or not shift.get("allow_overtime") or not shift.get("overtime_type"):
        doc.overtime_type = None
        return 0.0

    if not doc.get("in_time") or not doc.get("out_time"):
        # Leave, WFH, absent — no clock, so no overtime and nothing to show.
        doc.overtime_type = None
        return 0.0

    doc.overtime_type = shift.overtime_type

    # A shift with no start/end time cannot place the window at all. Guarding
    # here as _shift_duration_hours already does — without it the f-string below
    # builds "2026-08-10 None" and get_datetime raises.
    if not shift.get("start_time") or not shift.get("end_time"):
        return 0.0

    settings = frappe.db.get_value(
        "Overtime Type", shift.overtime_type,
        ["pre_shift_buffer_minutes", "post_shift_buffer_minutes", "maximum_overtime_hours_allowed"],
        as_dict=True,
    )
    if not settings:
        return 0.0

    pre_buf   = flt(settings.get("pre_shift_buffer_minutes") or 0)
    post_buf  = flt(settings.get("post_shift_buffer_minutes") or 0)
    daily_cap = flt(settings.get("maximum_overtime_hours_allowed") or 0)
    qualifying_hours = flt(shift.get("custom_ot_qualifying_hours") or 0)

    in_dt  = get_datetime(doc.in_time)
    out_dt = get_datetime(doc.out_time)

    # Anchor on attendance_date, not on the punch-in date. A night shift runs
    # 22:00-06:00 and its Attendance is dated to the day it STARTS, so an
    # employee punching in at 00:10 has in_dt.date() one day later than the
    # shift. Anchoring on the punch put the window on the wrong day entirely
    # and reported a late arrival as 49 minutes of EARLY overtime.
    work_date = getdate(doc.attendance_date) if doc.get("attendance_date") else in_dt.date()

    shift_start_dt = get_datetime(f"{work_date} {shift.start_time}")
    shift_end_dt   = get_datetime(f"{work_date} {shift.end_time}")

    if shift_end_dt <= shift_start_dt:
        shift_end_dt += timedelta(days=1)

    # Effective shift hours = shift duration - unpaid lunch break. _lunch_hours
    # is the single source of truth for that deduction across this module.
    shift_hours = (shift_end_dt - shift_start_dt).total_seconds() / 3600.0
    effective_shift_hours = shift_hours - _lunch_hours(shift)

    # Total worked hours — same source as the base pass (native working_hours,
    # break-aware, then net of the unpaid lunch). Must not diverge, or base + OT
    # stops reconciling to the hours the employee is actually paid for.
    total_worked_hours = _effective_worked_hours(doc, shift)

    # Buffers can be switched off per shift. Off means "no grace period":
    # in the window model any minute outside the shift window is overtime; in
    # the threshold model every minute past the qualifying hours is overtime.
    apply_buffers = cint(shift.get("custom_apply_ot_buffers", 1))
    if not apply_buffers:
        pre_buf = post_buf = 0.0

    early_minutes = (shift_start_dt - in_dt).total_seconds() / 60.0
    late_minutes = (out_dt - shift_end_dt).total_seconds() / 60.0

    if qualifying_hours > 0:
        # --- Threshold model -------------------------------------------------
        # Hours worked outside the shift window count toward the qualifying
        # hours exactly like hours inside it, so a late arrival is made up
        # before any overtime is earned. The worked figure is already net of the
        # unpaid lunch break, so the qualifying hours must be set to what a full
        # normal day yields AFTER that deduction.
        excess = max(total_worked_hours - qualifying_hours, 0.0)

        # The shift buffers still apply. Without them, first-in/last-out
        # measurement puts almost everyone a few minutes over the threshold and
        # every trivial overrun becomes overtime at the multiplier: measured on
        # August 2026, 1,265 OT hours across 3,702 days, of which 3,106 were
        # under half an hour. With the buffers, 621 hours across 585 days.
        if not apply_buffers:
            # No grace period AND no clock test: the shift window is ignored
            # entirely, exactly as the threshold model behaved before buffers
            # were wired in. Every minute past N is overtime, wherever it fell.
            total_ot = excess
            morning_ot = 0.0
            evening_ot = total_ot
        else:
            qualifying_early = (
                max((shift_start_dt - in_dt).total_seconds() / 3600.0, 0.0)
                if early_minutes > pre_buf
                else 0.0
            )
            qualifying_late = (
                max((out_dt - shift_end_dt).total_seconds() / 3600.0, 0.0)
                if late_minutes > post_buf
                else 0.0
            )
            qualifying_total = qualifying_early + qualifying_late

            # Take the SMALLER of the two measures. The excess caps the
            # clock-based figure, which is what stops someone who arrived two
            # hours late from claiming the whole after-shift period. The
            # buffers cap the excess, which is what stops a six-minute overrun
            # becoming paid overtime.
            total_ot = min(excess, qualifying_total)

            if total_ot > 0 and qualifying_total > 0:
                # Split proportionally so the early/late minute fields stay
                # meaningful — the threshold model otherwise reports everything
                # as late, losing the breakdown entirely.
                morning_ot = total_ot * (qualifying_early / qualifying_total)
                evening_ot = total_ot - morning_ot
            else:
                morning_ot = evening_ot = 0.0
    else:
        # --- Window model (legacy) -------------------------------------------
        # GUARD: Must work at least effective shift hours
        if total_worked_hours < effective_shift_hours:
            doc.custom_early_ot_minutes = 0
            doc.custom_late_ot_minutes  = 0
            doc.custom_total_ot_hours   = 0
            doc.custom_ot_status        = "Not Eligible"
            return 0.0

        morning_ot = 0.0
        evening_ot = 0.0

        if early_minutes > pre_buf:
            morning_ot = (shift_start_dt - in_dt).total_seconds() / 3600.0

        if late_minutes > post_buf:
            evening_ot = (out_dt - shift_end_dt).total_seconds() / 3600.0

        total_ot = morning_ot + evening_ot
        if total_ot < 0:
            total_ot = 0.0

    # Overtime can never exceed the hours actually worked. The window model
    # measures from the shift EDGES rather than from an excess over N, so a day
    # that both starts late and ends late reports more overtime than the
    # employee was on site for — sharply so once the unpaid lunch comes off the
    # worked figure. Base then floors at 0 and the day pays MORE than it was
    # worth, at the overtime multiplier: a 09:00-17:30 shift with a 60-minute
    # break, punched 17:00-02:00, is 8.00 effective hours but 8.50 h of clock
    # overtime. Trim proportionally so the early/late split survives.
    if total_ot > total_worked_hours:
        scale = (total_worked_hours / total_ot) if total_ot > 0 else 0.0
        morning_ot = morning_ot * scale
        evening_ot = evening_ot * scale
        total_ot = max(total_worked_hours, 0.0)

    uncapped_ot = total_ot

    if daily_cap and total_ot > daily_cap:
        # Hours trimmed by the cap are NOT paid. They must not fall back into
        # base_hours — that is exactly why base is stored by the base pass
        # rather than re-derived downstream as
        # (working_hours - custom_total_ot_hours).
        # Scale BOTH ends down in proportion. Taking evening whole and giving
        # morning whatever is left starved the early figure: a 07:30-21:00 day
        # (early 1.5, late 3.5, cap 4) reported early 0.50 / late 3.50 instead
        # of the 1.20 / 2.80 the split is meant to produce. The total was right
        # either way, so only the early/late breakdown was affected.
        scale = daily_cap / total_ot
        morning_ot = morning_ot * scale
        evening_ot = evening_ot * scale
        total_ot = daily_cap

    # round(), not int(): truncation dropped a whole minute whenever float
    # error left the product a hair under a round number — 0.6 h stored as
    # 35 minutes instead of 36. The money field (custom_total_ot_hours) was
    # always right; only these reporting fields drifted.
    #
    # Round the total ONCE and derive late from it, rather than rounding both
    # ends independently — two independent roundings can each go up and leave
    # early + late above the true total (measured: 1.63 minutes on a 97.07
    # minute day). This way the two fields always sum to the stored hours.
    total_minutes = int(round(total_ot * 60))
    doc.custom_early_ot_minutes = int(round(morning_ot * 60))
    doc.custom_late_ot_minutes  = max(total_minutes - doc.custom_early_ot_minutes, 0)
    doc.custom_total_ot_hours   = round(total_ot, 4)
    doc.custom_ot_status = "Pending" if total_ot > 0 else "Not Eligible"

    return uncapped_ot
