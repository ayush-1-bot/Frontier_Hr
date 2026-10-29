"""
Attendance-driven leave accrual
-------------------------------
All behaviour comes from an `Attendance Leave Accrual Policy` record.

Each submitted Attendance adds its status's credit to a counter on the
Employee. A full day adds 1, a half day 0.5, whatever the policy's table says.
When the counter reaches the threshold (20 by default) the employee is granted
a leave and the threshold comes off the counter — 20.5 grants once and keeps
0.5.

The grant tops up the employee's Leave Allocation the way hrms's own earned
leave does, so it shows in every balance screen. Writing a ledger entry alone
would not: hrms reads the allocated side from `Leave Allocation` rows only.

Idempotent in both directions. `custom_accrual_credit_days` on the Attendance
records what that row contributed, so a re-save never credits twice and a
cancel takes back exactly what was given — including clawing back a grant if
the cancellation drops the counter back below the threshold.
"""

import frappe
from frappe import _
from frappe.utils import add_days, flt, get_first_day, get_last_day, getdate

from frontier_hr.overtime_extension.overrides.attendance import _get_shift_settings

POLICY_DOCTYPE = "Attendance Leave Accrual Policy"
GRANT_LOG = "Attendance Leave Grant Log"

# Scope filters, most specific first; also the tie-break weight.
SCOPE_FIELDS = ("employee_grade", "shift_type", "department", "branch")


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def accrue_on_submit(doc, method=None):
	policy = _resolve_policy(doc)
	if not policy:
		return

	credit = _credit_for(doc, policy)
	already = flt(doc.get("custom_accrual_credit_days"))
	if abs(credit - already) < 0.0001:
		return  # nothing changed

	_apply_credit(doc, policy, credit - already)
	doc.db_set("custom_accrual_credit_days", credit, update_modified=False)


def reverse_on_cancel(doc, method=None):
	"""Take back exactly what this row gave, grants included."""
	credit = flt(doc.get("custom_accrual_credit_days"))
	if not credit:
		return

	policy = _resolve_policy(doc)
	if policy:
		_apply_credit(doc, policy, -credit)
	doc.db_set("custom_accrual_credit_days", 0, update_modified=False)


# ---------------------------------------------------------------------------
# Counter
# ---------------------------------------------------------------------------

def _apply_credit(doc, policy, delta):
	"""Move the counter by `delta`, then settle any grants it crosses."""
	employee = frappe.get_doc("Employee", doc.employee)
	period = _period_key(doc, policy)

	# A new period starts the counter again, carrying the leftover only when
	# the policy says to. Done on the first touch of the period rather than on
	# a schedule, so nothing depends on a job having run.
	counter = flt(employee.get("custom_attendance_credit_days"))
	earned = flt(employee.get("custom_attendance_leaves_earned"))
	if employee.get("custom_attendance_credit_period") != period:
		counter = counter if policy.carry_forward_counter else 0.0
		earned = 0.0  # the per-period cap starts again

	counter += delta
	counter, earned = _settle(doc, policy, employee, counter, earned, period)

	employee.db_set("custom_attendance_credit_days", flt(counter, 3), update_modified=False)
	employee.db_set("custom_attendance_leaves_earned", flt(earned, 3), update_modified=False)
	employee.db_set("custom_attendance_credit_period", period, update_modified=False)
	employee.db_set("custom_attendance_accrual_policy", policy.name, update_modified=False)


def _settle(doc, policy, employee, counter, earned, period):
	"""Grant while the counter is over the threshold; claw back while it is
	negative because a cancellation removed the days that earned a grant."""
	threshold = flt(policy.days_required)
	granted = flt(policy.leaves_granted)
	cap = flt(policy.max_leaves_per_period)

	while counter >= threshold:
		if cap and earned + granted > cap + 0.0001:
			break  # the period's cap is reached; the counter simply keeps growing
		before = counter
		after = 0.0 if not policy.carry_forward_remainder else counter - threshold
		if not _grant(doc, policy, employee, granted, before, after, period):
			break  # allocation refused it; leave the counter alone to retry later
		counter = after
		earned += granted

	while counter < 0 and earned >= granted - 0.0001:
		before = counter
		after = counter + threshold
		if not _grant(doc, policy, employee, -granted, before, after, period):
			break
		counter = after
		earned -= granted

	return counter, earned


def _period_key(doc, policy):
	"""Which reset period this Attendance falls in."""
	date = getdate(doc.attendance_date)
	if policy.reset_period == "Monthly":
		return date.strftime("%Y-%m")
	if policy.reset_period == "Yearly":
		return date.strftime("%Y")
	return "all"


# ---------------------------------------------------------------------------
# Granting
# ---------------------------------------------------------------------------

def _grant(doc, policy, employee, leaves, counter_before, counter_after, period):
	"""Returns True only when the leave actually reached an allocation, so a
	refusal leaves the counter untouched and the day can be retried."""
	date = getdate(doc.attendance_date)
	allocation = _find_allocation(employee.name, policy, date)

	if allocation:
		_top_up(allocation, leaves, date)
	else:
		# hrms refuses an allocation of zero, so a first grant has to create it
		# with the leaves already on it rather than topping up an empty one.
		allocation = _create_allocation(employee.name, policy, date, leaves)
		if not allocation:
			return False

	frappe.get_doc(
		{
			"doctype": GRANT_LOG,
			"employee": employee.name,
			"employee_name": employee.employee_name,
			"grant_date": doc.attendance_date,
			"policy": policy.name,
			"leave_type": policy.leave_type,
			"leaves_granted": flt(leaves, 3),
			"counter_before": flt(counter_before, 3),
			"counter_after": flt(counter_after, 3),
			"triggered_by": doc.name,
			"leave_allocation": allocation.name,
			"reversed": 1 if leaves < 0 else 0,
		}
	).insert(ignore_permissions=True)
	return True


def _top_up(allocation, leaves, date):
	"""Add to an existing allocation exactly the way hrms's earned leave does:
	raise total_leaves_allocated, then write the matching ledger entry. hrms
	reads the allocated side from Leave Allocation rows, so a bare ledger entry
	would never reach a balance."""
	from hrms.hr.utils import create_additional_leave_ledger_entry

	allocation.db_set(
		"total_leaves_allocated",
		flt(allocation.total_leaves_allocated) + flt(leaves),
		update_modified=False,
	)
	create_additional_leave_ledger_entry(allocation, flt(leaves), date)


def _find_allocation(employee, policy, date):
	"""The submitted allocation covering the date, if any."""
	name = frappe.db.get_value(
		"Leave Allocation",
		{
			"employee": employee,
			"leave_type": policy.leave_type,
			"docstatus": 1,
			"from_date": ["<=", date],
			"to_date": [">=", date],
		},
		"name",
	)
	return frappe.get_doc("Leave Allocation", name) if name else None


def _create_allocation(employee, policy, date, leaves):
	"""Open the year's allocation with the first grant already on it."""
	if flt(leaves) <= 0:
		# Nothing to claw back from: the allocation that earned it is gone.
		return None

	try:
		return frappe.get_doc(
			{
				"doctype": "Leave Allocation",
				"employee": employee,
				"leave_type": policy.leave_type,
				"from_date": date.replace(month=1, day=1),
				"to_date": date.replace(month=12, day=31),
				"new_leaves_allocated": flt(leaves),
				"company": policy.company,
				"carry_forward": 0,
			}
		).insert(ignore_permissions=True).submit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Attendance Leave Accrual: allocation failed")
		return None


# ---------------------------------------------------------------------------
# Policy and credit
# ---------------------------------------------------------------------------

def _credit_for(doc, policy):
	"""Days this Attendance earns, from the policy's table."""
	status = doc.get("status")
	for row in policy.credits:
		if row.attendance_status != status:
			continue
		if status == "Half Day" and doc.get("half_day_status") == "Present" and flt(row.half_day_credit):
			# The other half was paid leave, which the policy may rate higher.
			return flt(row.half_day_credit)
		return flt(row.credit)
	return 0.0


def _resolve_policy(doc):
	"""The enabled policy whose every set filter matches this row.

	Highest priority wins; ties go to the one pinning more scope fields.
	"""
	if not (doc.get("company") and doc.get("employee")):
		return None

	candidates = frappe.get_all(
		POLICY_DOCTYPE,
		filters={"enabled": 1, "company": doc.company},
		fields=["name", "priority"] + list(SCOPE_FIELDS),
	)
	if not candidates:
		return None

	shift = _get_shift_settings(doc)
	employee = frappe.db.get_value("Employee", doc.employee, ["branch", "grade"], as_dict=True) or {}
	actual = {
		"branch": employee.get("branch"),
		"department": doc.get("department"),
		"shift_type": shift.get("name") if shift else None,
		"employee_grade": employee.get("grade"),
	}

	matches = []
	for row in candidates:
		if any(row.get(f) and row.get(f) != actual.get(f) for f in SCOPE_FIELDS):
			continue
		matches.append((int(row.priority or 0), sum(1 for f in SCOPE_FIELDS if row.get(f)), row.name))

	if not matches:
		return None

	matches.sort(reverse=True)
	return frappe.get_cached_doc(POLICY_DOCTYPE, matches[0][2])
