"""
Late Coming and Early Out deduction
-----------------------------------
Everything here is read from a `Late Coming and Early Out Deduction Policy`
record. Nothing about grace, counts, ranges or the deduction order is decided
in this file — it only walks what HR configured.

The pass runs on every Attendance save and submit, AFTER
overtime_extension.overrides.attendance.apply_buffer_logic, because it
subtracts from the `working_hours` and `custom_base_hours` that pass writes.

  1. RESOLVE   the policy for this employee/day (company, branch, department,
               shift, grade). The most specific enabled match wins.
  2. MEASURE   minutes late = in_time - shift start - late grace,
               minutes early = shift end - out_time - early grace.
  3. ALLOW     if the employee is still inside the free count for the period
               (Monthly / Weekly / Payroll Period), the minutes are recorded
               and nothing is deducted.
  4. SLAB      the minutes land in a Deduction Range row, which says how many
               minutes come off. 1-30 -> 30, 31-60 -> 60, and so on.
  5. WALK      the Deduction Priority table, top to bottom. A Leave row is
               used only as far as the employee's balance stretches; whatever
               it cannot cover falls to the next row. The Working Hours row
               absorbs the remainder.

The leave balance is only reserved during the save. Nothing is written to the
ledger until the Attendance is submitted (`post_leave_ledger`), and cancelling
the Attendance returns the leave (`cancel_leave_ledger`).
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_datetime, get_first_day, get_last_day, getdate
from datetime import timedelta

from frontier_hr.overtime_extension.overrides.attendance import _get_shift_settings

# Statuses with no punch pair to judge. A Half Day still has an in/out pair and
# can be late, so it is deliberately not in this set.
NO_PUNCH_STATUSES = ("Absent", "On Leave")

POLICY_DOCTYPE = "Late Coming and Early Out Deduction Policy"

# Scope filters, most specific first. Also the tie-break weight: a policy that
# pins the shift beats one that only pins the company.
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

	late = _late_minutes(doc, shift, policy)
	early = _early_minutes(doc, shift, policy)
	doc.custom_late_coming_minutes = late
	doc.custom_early_out_minutes = early
	if not (late or early):
		return

	minutes = 0
	if late and not _within_allowance(doc, policy, "late"):
		minutes += _slab_minutes(policy, "Late Coming", late)
	if early and not _within_allowance(doc, policy, "early"):
		minutes += _slab_minutes(policy, "Early Out", early)

	doc.custom_deduction_minutes = minutes
	if minutes:
		_walk_priority(doc, policy, minutes)


def _posted_leaves(attendance, leave_type):
	"""Net leaves this Attendance has already moved for one leave type.
	Negative means debited, 0 means never posted or fully reversed."""
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
	"""Debit the leave the save pass reserved.

	Posts the DIFFERENCE between what the breakup says is owed and what this
	Attendance has already moved, so a re-submit after a cancel debits again
	(the cancel left a reversal, netting to zero) while a repeated submit of an
	unchanged row does nothing.
	"""
	for row in doc.get("custom_deduction_breakup") or []:
		if row.source != "Leave" or not flt(row.leave_days):
			continue

		outstanding = -flt(row.leave_days) - _posted_leaves(doc.name, row.leave_type)
		if abs(outstanding) < 0.0001:
			continue
		_write_ledger(doc, row.leave_type, outstanding)


def cancel_leave_ledger(doc, method=None):
	"""Give the leave back by DELETING this Attendance's ledger rows.

	The same thing hrms does in leave_ledger_entry.delete_ledger_entry when a
	Leave Application is cancelled, and for the same two reasons: a Leave
	Ledger Entry refuses to be cancelled ("Only expired allocation can be
	cancelled"), and a reversing entry cannot be inserted from on_cancel either
	— the Attendance is already docstatus 2 by then, so the new row's link to
	it fails with "Cannot link cancelled document".

	Leaves post_leave_ledger's net at zero, so amending and re-submitting the
	day debits the leave again.
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

	Highest `priority` wins; on a tie the policy that pins more scope fields
	wins, so a shift-specific rule beats a company-wide one without HR having
	to number them.
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

	# Attendance carries department but not branch or grade, so those two come
	# off the Employee.
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
		# Night shift: Attendance is dated to the day the shift starts.
		end += timedelta(days=1)
	return start, end


def _late_minutes(doc, shift, policy):
	if not doc.get("in_time"):
		return 0
	start, _end = _shift_window(doc, shift)
	late = (get_datetime(doc.in_time) - start).total_seconds() / 60.0
	return max(int(late - cint(policy.late_grace_minutes)), 0)


def _early_minutes(doc, shift, policy):
	if not doc.get("out_time"):
		return 0
	_start, end = _shift_window(doc, shift)
	early = (end - get_datetime(doc.out_time)).total_seconds() / 60.0
	return max(int(early - cint(policy.early_grace_minutes)), 0)


# ---------------------------------------------------------------------------
# Allowed count
# ---------------------------------------------------------------------------

def _period_range(doc, policy):
	date = getdate(doc.attendance_date)

	if policy.allowance_period == "Weekly":
		# Sunday-Saturday, the same boundary overtime_extension.tasks uses.
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
		# No Payroll Period covers the date — fall through to the month rather
		# than silently handing out an unlimited allowance.

	return get_first_day(date), get_last_day(date)


def _within_allowance(doc, policy, kind):
	"""True while the employee still has free occurrences left this period.

	Counts only days BEFORE this one, so the free allowance always goes to the
	earliest occurrences in the period no matter what order HR saves the rows in.
	"""
	allowed = cint(policy.allowed_late_count if kind == "late" else policy.allowed_early_count)
	if policy.count_together:
		allowed = cint(policy.allowed_late_count)
	if allowed <= 0:
		return False

	start, end = _period_range(doc, policy)
	if policy.count_together:
		condition = "(custom_late_coming_minutes > 0 OR custom_early_out_minutes > 0)"
	elif kind == "late":
		condition = "custom_late_coming_minutes > 0"
	else:
		condition = "custom_early_out_minutes > 0"

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

	return cint(used) < allowed


# ---------------------------------------------------------------------------
# Deduction Range
# ---------------------------------------------------------------------------

def _slab_minutes(policy, scope, minutes):
	"""Minutes to deduct for `minutes` of lateness. 0 when no range covers it,
	which is how HR switches a band off — a gap in the table means no penalty."""
	for row in policy.deduction_slabs:
		if row.applies_to not in (scope, "Both"):
			continue
		if minutes < cint(row.from_minutes):
			continue
		# to_minutes 0 is the catch-all top range.
		if row.to_minutes and minutes > cint(row.to_minutes):
			continue
		return cint(row.deduct_minutes)
	return 0


# ---------------------------------------------------------------------------
# Deduction Priority walk
# ---------------------------------------------------------------------------

def _walk_priority(doc, policy, minutes):
	remaining = float(minutes)

	for row in policy.deduction_priority:
		# Sub-minute leftovers are float noise from the days<->minutes round
		# trip, not a real debt to pass down the table.
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

	hours_per_unit, days_per_unit = flt(row.hours_per_unit), flt(row.days_per_unit)
	if hours_per_unit <= 0 or days_per_unit <= 0:
		# The policy's validate() rejects this; guard anyway so a row edited
		# straight in the database cannot divide by zero mid-payroll.
		return remaining

	# days <-> minutes both ways through the row's own rate, so a partly
	# covered deduction hands the next row exactly what the balance missed.
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
	"""Working Hours always clears the remainder: the hours come off the day's
	pay, and there is nothing further down the table to hand a shortfall to.

	Only `custom_base_hours` is reduced, never native `working_hours`. The
	overtime pass rebuilds custom_base_hours from the punches on every save, so
	subtracting from it here is idempotent. working_hours is not: the overtime
	pass only recomputes it on punched days under "First Check-in and Last
	Check-out" (see _override_applies), and on every other row it would carry
	the previous save's deduction and lose the hours again on each re-save.
	working_hours therefore stays the measured day; custom_base_hours is the
	payable day, which is what the Salary Slip sums.

	This does nothing on a Present / Absent shift, where custom_base_hours is
	forced to 0 and the day is paid whole — put a leave row last in the
	priority table for those shifts.
	"""
	hours = remaining / 60.0

	doc.custom_base_hours = max(flt(doc.get("custom_base_hours")) - hours, 0.0)
	doc.custom_deducted_hours = flt(hours, 3)

	doc.append(
		"custom_deduction_breakup",
		{"source": "Working Hours", "minutes": int(round(remaining)), "hours": flt(hours, 3)},
	)
	return 0.0


def _leave_balance(doc, leave_type):
	"""Balance on the attendance date, straight off the ledger.

	Deliberately not hrms's get_leave_balance_on: that runs a role check that
	throws for anyone without leave-approver rights, and this runs on every
	Attendance save. Entries this same Attendance already posted are excluded,
	so re-saving a submitted row reads the balance it started from instead of
	one already reduced by its own debit.
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
	"""Every save recomputes from scratch, so a policy edit or a corrected punch
	cannot leave a stale deduction behind."""
	doc.custom_deduction_policy = None
	doc.custom_late_coming_minutes = 0
	doc.custom_early_out_minutes = 0
	doc.custom_deduction_minutes = 0
	doc.custom_deducted_leave_days = 0
	doc.custom_deducted_hours = 0
	doc.custom_deduction_breakup = []
