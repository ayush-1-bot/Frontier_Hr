"""
Late Coming and Early Out deduction
-----------------------------------
All behaviour comes from a `Late Coming and Early Out Deduction Policy` record;
nothing is decided here.

Runs on every Attendance save/submit, after apply_buffer_logic, whose
custom_base_hours it subtracts from.

  1. RESOLVE  the policy for this employee/day.
  2. MEASURE  minutes late/early against the shift, less the grace.
  3. SLAB     look the minutes up in the Deduction Range table.
  4. WALK     the Deduction Priority table, top to bottom.

Leave is only reserved on save; it reaches the ledger on submit and comes back
on cancel.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_datetime, get_first_day, get_last_day, getdate
from datetime import timedelta

from frontier_hr.overtime_extension.overrides.attendance import _get_shift_settings

# No punch pair to judge. Half Day has one and can be late, so it is excluded.
NO_PUNCH_STATUSES = ("Absent", "On Leave")

POLICY_DOCTYPE = "Late Coming and Early Out Deduction Policy"

# Deduct the minutes actually lost, rather than the Deduction Range figure.
ACTUAL_MINUTES = "Actual Late Minutes"

# Scope filters, most specific first; also the tie-break weight.
SCOPE_FIELDS = ("employee_grade", "shift_type", "department", "branch")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def apply_deduction(doc, method=None):
	_clear_fields(doc)

	if doc.get("status") in NO_PUNCH_STATUSES:
		return

	shift = _get_shift_settings(doc)
	if not shift or not shift.get("start_time") or not shift.get("end_time"):
		return

	policy = _resolve_policy(doc, shift)
	if not policy:
		return

	doc.custom_deduction_policy = policy.name

	# Grace is a quota, not a daily tolerance — see _grace_for.
	late_raw = _late_minutes(doc, shift, policy, grace=0)
	early_raw = _early_minutes(doc, shift, policy, grace=0)
	doc.custom_late_coming_raw_minutes = late_raw
	doc.custom_early_out_raw_minutes = early_raw

	late = max(late_raw - _grace_for(doc, policy, "late", late_raw), 0)
	early = max(early_raw - _grace_for(doc, policy, "early", early_raw), 0)
	doc.custom_late_coming_minutes = late
	doc.custom_early_out_minutes = early
	if not (late or early):
		return

	if policy.deduction_basis == ACTUAL_MINUTES:
		# Deduct exactly what was lost, no rounding up to a range.
		minutes = late + early
	else:
		minutes = _slab_minutes(policy, "Late Coming", late) if late else 0
		minutes += _slab_minutes(policy, "Early Out", early) if early else 0

	doc.custom_deduction_minutes = minutes
	doc.custom_deduction_report_only = cint(policy.report_only)
	if minutes:
		_walk_priority(doc, policy, minutes)


def _posted_leaves(attendance, leave_type):
	"""Net leaves this Attendance has moved. Negative = debited, 0 = none/reversed."""
	total = frappe.db.sql(
		"""
		SELECT SUM(leaves) FROM `tabLeave Ledger Entry`
		WHERE docstatus = 1
		  AND transaction_type = 'Attendance'
		  AND transaction_name = %(name)s
		  AND leave_type = %(leave_type)s
		""",
		{"name": attendance, "leave_type": leave_type},
	)[0][0]
	return flt(total)


def _write_ledger(doc, leave_type, leaves):
	frappe.get_doc(
		{
			"doctype": "Leave Ledger Entry",
			"employee": doc.employee,
			"employee_name": doc.get("employee_name"),
			"leave_type": leave_type,
			"transaction_type": "Attendance",
			"transaction_name": doc.name,
			"leaves": leaves,
			"from_date": doc.attendance_date,
			"to_date": doc.attendance_date,
			"is_lwp": 0,
			"company": doc.company,
		}
	).insert(ignore_permissions=True).submit()


def post_leave_ledger(doc, method=None):
	"""Debit the reserved leave.

	Posts the difference between what is owed and what this row already moved,
	so a re-submit after a cancel debits again but a repeat submit does nothing.
	"""
	if cint(doc.get("custom_deduction_report_only")):
		return

	for row in doc.get("custom_deduction_breakup") or []:
		if row.source != "Leave" or not flt(row.leave_days):
			continue

		outstanding = -flt(row.leave_days) - _posted_leaves(doc.name, row.leave_type)
		if abs(outstanding) < 0.0001:
			continue
		_write_ledger(doc, row.leave_type, outstanding)


def cancel_leave_ledger(doc, method=None):
	"""Delete this row's ledger entries, as hrms does for a Leave Application.

	A Leave Ledger Entry cannot be cancelled, and a reversing entry cannot be
	inserted from on_cancel (the Attendance is already docstatus 2). Leaves the
	net at zero, so re-submitting debits again.
	"""
	frappe.db.sql(
		"""
		DELETE FROM `tabLeave Ledger Entry`
		WHERE transaction_type = 'Attendance' AND transaction_name = %s
		""",
		doc.name,
	)


# ---------------------------------------------------------------------------
# Policy resolution
# ---------------------------------------------------------------------------

def _resolve_policy(doc, shift):
	"""The enabled policy whose every set filter matches this row.

	Highest priority wins; ties go to the one pinning more scope fields, so a
	shift rule beats a company-wide one without HR numbering them.
	"""
	if not doc.get("company"):
		return None

	candidates = frappe.get_all(
		POLICY_DOCTYPE,
		filters={"enabled": 1, "company": doc.company},
		fields=["name", "priority"] + list(SCOPE_FIELDS),
	)
	if not candidates:
		return None

	# Attendance has department but not branch/grade.
	employee = frappe.db.get_value("Employee", doc.employee, ["branch", "grade"], as_dict=True) or {}
	actual = {
		"branch": employee.get("branch"),
		"department": doc.get("department"),
		"shift_type": shift.get("name"),
		"employee_grade": employee.get("grade"),
	}

	matches = []
	for row in candidates:
		if any(row.get(f) and row.get(f) != actual.get(f) for f in SCOPE_FIELDS):
			continue
		specificity = sum(1 for f in SCOPE_FIELDS if row.get(f))
		matches.append((cint(row.priority), specificity, row.name))

	if not matches:
		return None

	matches.sort(reverse=True)
	return frappe.get_cached_doc(POLICY_DOCTYPE, matches[0][2])


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

def _shift_window(doc, shift):
	"""Shift start and end as datetimes on this Attendance row's date."""
	work_date = getdate(doc.attendance_date)
	start = get_datetime(f"{work_date} {shift.start_time}")
	end = get_datetime(f"{work_date} {shift.end_time}")
	if end <= start:
		# Night shift: the row is dated to the day the shift starts.
		end += timedelta(days=1)
	return start, end


def _late_minutes(doc, shift, policy, grace=None):
	if not doc.get("in_time"):
		return 0
	start, _end = _shift_window(doc, shift)
	late = (get_datetime(doc.in_time) - start).total_seconds() / 60.0
	if grace is None:
		grace = cint(policy.late_grace_minutes)
	return max(int(late - grace), 0)


def _early_minutes(doc, shift, policy, grace=None):
	if not doc.get("out_time"):
		return 0
	_start, end = _shift_window(doc, shift)
	early = (end - get_datetime(doc.out_time)).total_seconds() / 60.0
	if grace is None:
		grace = cint(policy.early_grace_minutes)
	return max(int(early - grace), 0)


# ---------------------------------------------------------------------------
# Allowed count
# ---------------------------------------------------------------------------

def _period_range(doc, policy):
	date = getdate(doc.attendance_date)

	if policy.allowance_period == "Weekly":
		# Sunday-Saturday, as overtime_extension.tasks uses.
		sunday = add_days(date, -((date.weekday() + 1) % 7))
		return sunday, add_days(sunday, 6)

	if policy.allowance_period == "Payroll Period":
		period = frappe.db.sql(
			"""
			SELECT start_date, end_date FROM `tabPayroll Period`
			WHERE company = %(company)s AND %(date)s BETWEEN start_date AND end_date
			LIMIT 1
			""",
			{"company": doc.company, "date": date},
			as_dict=True,
		)
		if period:
			return period[0].start_date, period[0].end_date
		# None covers the date: fall back to the month, not an unlimited quota.

	return get_first_day(date), get_last_day(date)


def _grace_for(doc, policy, kind, raw_minutes):
	"""Grace this day gets: the full amount while allowed times remain, else 0 —
	which is what makes even one minute count once they run out.

	A day beyond the grace keeps it regardless; withdrawing it there would
	punish one big lateness for someone else's small ones.
	"""
	grace = cint(policy.late_grace_minutes if kind == "late" else policy.early_grace_minutes)
	if not grace or raw_minutes <= 0:
		return grace

	allowed = cint(
		policy.allowed_late_count
		if (kind == "late" or policy.count_together)
		else policy.allowed_early_count
	)
	if allowed <= 0:
		return grace  # 0 = never runs out
	if raw_minutes > grace:
		return grace  # beyond the grace: charged today, uses no time up

	return grace if _grace_used(doc, policy, kind) < allowed else 0


def _grace_used(doc, policy, kind):
	"""Earlier days in the period the grace absorbed entirely — these use the
	quota up. Only days before this one, so the allowed times always go to the
	earliest occurrences whatever order HR saves rows in.
	"""
	start, end = _period_range(doc, policy)

	def clause(prefix):
		return f"(custom_{prefix}_raw_minutes > 0 AND custom_{prefix}_minutes = 0)"

	if policy.count_together:
		condition = f"({clause('late_coming')} OR {clause('early_out')})"
	else:
		condition = clause("late_coming" if kind == "late" else "early_out")

	used = frappe.db.sql(
		f"""
		SELECT COUNT(*) FROM `tabAttendance`
		WHERE employee = %(employee)s
		  AND docstatus < 2
		  AND name != %(name)s
		  AND attendance_date BETWEEN %(start)s AND %(end)s
		  AND attendance_date < %(date)s
		  AND {condition}
		""",
		{
			"employee": doc.employee,
			"name": doc.get("name") or "",
			"start": start,
			"end": end,
			"date": getdate(doc.attendance_date),
		},
	)[0][0]
	return cint(used)


# ---------------------------------------------------------------------------
# Deduction Range
# ---------------------------------------------------------------------------

def _slab_minutes(policy, scope, minutes):
	"""Minutes to deduct for this lateness. 0 when no range covers it, which is
	how HR switches a band off."""
	for row in policy.deduction_slabs:
		if row.applies_to not in (scope, "Both"):
			continue
		if minutes < cint(row.from_minutes):
			continue
		if row.to_minutes and minutes > cint(row.to_minutes):  # 0 = catch-all
			continue
		return cint(row.deduct_minutes)
	return 0


# ---------------------------------------------------------------------------
# Deduction Priority walk
# ---------------------------------------------------------------------------

def _walk_priority(doc, policy, minutes):
	"""Fill in the breakup: which source covers how much.

	Runs the same under Report Only, but nothing is actually taken —
	custom_base_hours is left alone and post_leave_ledger writes nothing.
	"""
	remaining = float(minutes)

	for row in policy.deduction_priority:
		# Sub-minute leftovers are float noise, not a real debt.
		if remaining < 1:
			break

		if row.source == "Working Hours":
			remaining = _take_from_working_hours(doc, remaining)
			continue

		remaining = _take_from_leave(doc, row, remaining)

	doc.custom_deducted_leave_days = flt(
		sum(flt(r.leave_days) for r in doc.get("custom_deduction_breakup") or []), 3
	)


def _take_from_leave(doc, row, remaining):
	balance = _leave_balance(doc, row.leave_type)
	if balance <= 0:
		return remaining
	# Report Only never debits, so every day reads the same untouched balance:
	# a multi-day report shows what each day could take, not a running total.

	hours_per_unit, days_per_unit = flt(row.hours_per_unit), flt(row.days_per_unit)
	if hours_per_unit <= 0 or days_per_unit <= 0:
		# validate() rejects this; guard against a direct database edit.
		return remaining

	# Convert both ways through this row's own rate, so a partly covered
	# deduction hands the next row exactly what the balance missed.
	days_needed = remaining / 60.0 / hours_per_unit * days_per_unit
	days_taken = min(days_needed, balance)
	minutes_covered = days_taken / days_per_unit * hours_per_unit * 60.0

	doc.append(
		"custom_deduction_breakup",
		{
			"source": "Leave",
			"leave_type": row.leave_type,
			"minutes": int(round(minutes_covered)),
			"leave_days": flt(days_taken, 3),
		},
	)
	return remaining - minutes_covered


def _take_from_working_hours(doc, remaining):
	"""Clears the remainder — nothing below it can take a shortfall.

	Reduces custom_base_hours only, never native working_hours. The overtime
	pass rebuilds custom_base_hours from the punches every save, so subtracting
	here is idempotent; working_hours is not rebuilt on every row and would
	compound. working_hours stays the measured day, custom_base_hours the
	payable one.

	Inert on a Present / Absent shift, where custom_base_hours is forced to 0 —
	use a leave row there instead.
	"""
	hours = remaining / 60.0

	if not cint(doc.get("custom_deduction_report_only")):
		doc.custom_base_hours = max(flt(doc.get("custom_base_hours")) - hours, 0.0)
	doc.custom_deducted_hours = flt(hours, 3)

	doc.append(
		"custom_deduction_breakup",
		{"source": "Working Hours", "minutes": int(round(remaining)), "hours": flt(hours, 3)},
	)
	return 0.0


def _leave_balance(doc, leave_type):
	"""Balance on the attendance date, straight off the ledger.

	Not hrms's get_leave_balance_on: that runs a role check that would throw for
	non leave-approvers on every save. This row's own entries are excluded, so a
	re-save reads the balance it started from.
	"""
	balance = frappe.db.sql(
		"""
		SELECT SUM(leaves) FROM `tabLeave Ledger Entry`
		WHERE docstatus = 1
		  AND employee = %(employee)s
		  AND leave_type = %(leave_type)s
		  AND is_expired = 0
		  AND %(date)s BETWEEN from_date AND to_date
		  AND NOT (transaction_type = 'Attendance' AND transaction_name = %(name)s)
		""",
		{
			"employee": doc.employee,
			"leave_type": leave_type,
			"date": getdate(doc.attendance_date),
			"name": doc.get("name") or "",
		},
	)[0][0]
	return max(flt(balance), 0.0)


# ---------------------------------------------------------------------------

def _clear_fields(doc):
	"""Recompute from scratch each save, so a policy edit or corrected punch
	leaves no stale deduction."""
	doc.custom_deduction_policy = None
	doc.custom_late_coming_raw_minutes = 0
	doc.custom_late_coming_minutes = 0
	doc.custom_early_out_raw_minutes = 0
	doc.custom_early_out_minutes = 0
	doc.custom_deduction_minutes = 0
	doc.custom_deduction_report_only = 0
	doc.custom_deducted_leave_days = 0
	doc.custom_deducted_hours = 0
	doc.custom_deduction_breakup = []
