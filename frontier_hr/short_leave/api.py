"""Short leave: a few hours off, taken against a monthly quota.

hrms has no short leave. Its smallest unit is the Half Day on a Leave
Application, so "two hours off on Tuesday" cannot be asked for at all. This
module adds it: a `Short Leave Policy` says how many short leaves an employee
gets per period and how long one is, and a `Short Leave Request` spends them.

Nothing is allocated and nothing is carried forward. The balance is derived by
summing the period's submitted requests, so a new period starts full and an
unused quota simply stops being reachable -- which is what "expires monthly"
means, with no scheduled job to run and nothing to go stale.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, get_first_day, get_last_day, getdate

from frontier_hr.policy_scope import pinned_shifts

POLICY_DOCTYPE = "Short Leave Policy"
REQUEST_DOCTYPE = "Short Leave Request"

# Scope filters, most specific first; also the tie-break weight.
SCOPE_FIELDS = ("employee_grade", "department", "branch")

WHOLE_UNIT = "Whole Short Leave"


def resolve_policy(employee, date, shift=None):
	"""The enabled policy whose every set filter matches this employee's day.

	Highest priority wins; ties go to the one pinning more scope fields, so a
	rule for one grade beats a company-wide one without HR numbering them.
	"""
	details = frappe.db.get_value(
		"Employee", employee, ["company", "branch", "department", "grade", "default_shift"], as_dict=True
	)
	if not details:
		return None

	candidates = frappe.get_all(
		POLICY_DOCTYPE,
		filters={"enabled": 1, "company": details.company},
		fields=["name", "priority"] + list(SCOPE_FIELDS),
	)
	if not candidates:
		return None

	actual = {
		"branch": details.branch,
		"department": details.department,
		"employee_grade": details.grade,
	}
	actual_shift = shift or _shift_on(employee, date)

	pinned = pinned_shifts(POLICY_DOCTYPE, [row.name for row in candidates])

	matches = []
	for row in candidates:
		shifts = pinned.get(row.name)
		if shifts and actual_shift not in shifts:
			continue
		if any(row.get(f) and row.get(f) != actual.get(f) for f in SCOPE_FIELDS):
			continue
		specificity = sum(1 for f in SCOPE_FIELDS if row.get(f)) + (1 if shifts else 0)
		matches.append((cint(row.priority), specificity, row.name))

	if not matches:
		return None

	matches.sort(reverse=True)
	return frappe.get_cached_doc(POLICY_DOCTYPE, matches[0][2])


def _shift_on(employee, date):
	"""The shift the employee is actually on that day.

	NOT Employee.default_shift: a Shift Assignment beats the default, so a
	policy pinned to one shift would otherwise accept a request from someone
	the Attendance side then refuses to excuse -- the request approved, the
	lateness still charged, and nothing on either document saying why.

	hrms owns this resolution, so it is asked rather than reimplemented.
	"""
	from frontier_hr.overtime_extension.overrides.attendance import _resolve_shift_name

	# The same resolver the Attendance side uses, so both ends of the feature
	# agree about which shift a day belongs to. hrms's get_employee_shift is
	# not it: that one picks the shift whose clock window contains a given
	# timestamp, so asking it about a day rather than a moment answers "none"
	# for every employee whose shift does not happen to cover that hour.
	return _resolve_shift_name(frappe._dict(employee=employee, attendance_date=getdate(date)))


def period_range(policy, date):
	"""The window the quota is counted over. Nothing crosses it."""
	date = getdate(date)

	if policy.reset_period == "Yearly":
		return date.replace(month=1, day=1), date.replace(month=12, day=31)

	if policy.reset_period == "Payroll Period":
		period = frappe.db.sql(
			"""
			SELECT start_date, end_date FROM `tabPayroll Period`
			WHERE company = %(company)s AND %(date)s BETWEEN start_date AND end_date
			LIMIT 1
			""",
			{"company": policy.company, "date": date},
			as_dict=True,
		)
		if period:
			return period[0].start_date, period[0].end_date
		# No period covers the date: fall back to the month rather than
		# handing out an unlimited quota.

	return get_first_day(date), get_last_day(date)


def units_for(policy, hours):
	"""What a request of this length costs against the quota."""
	if policy.consumption == WHOLE_UNIT:
		# However short the request, it burns a whole short leave -- so two
		# ten minute errands use the month up just as two full ones would.
		return 1.0
	per_unit = flt(policy.hours_per_short_leave)
	return flt(hours) / per_unit if per_unit else 0.0


@frappe.whitelist()
def get_balance(employee, date=None, policy=None, ignore_request=None):
	"""What is left of this employee's quota in the period `date` falls in.

	Only SUBMITTED requests count. A draft reserves nothing, and the balance is
	re-checked on submit, so two drafts cannot both slip through.
	"""
	date = getdate(date) if date else getdate(frappe.utils.today())
	policy = policy or resolve_policy(employee, date)
	if not policy:
		return None
	if isinstance(policy, str):
		policy = frappe.get_cached_doc(POLICY_DOCTYPE, policy)

	start, end = period_range(policy, date)
	used = frappe.db.sql(
		"""
		SELECT SUM(units_consumed) FROM `tabShort Leave Request`
		WHERE employee = %(employee)s AND docstatus = 1
		  AND date BETWEEN %(start)s AND %(end)s
		  AND name != %(ignore)s
		""",
		{"employee": employee, "start": start, "end": end, "ignore": ignore_request or ""},
	)[0][0]

	allowed = flt(policy.short_leaves_per_period)
	used = flt(used)
	return frappe._dict(
		policy=policy.name,
		period_start=start,
		period_end=end,
		hours_per_short_leave=flt(policy.hours_per_short_leave),
		allowed=allowed,
		used=used,
		remaining=max(allowed - used, 0.0),
		remaining_hours=max(allowed - used, 0.0) * flt(policy.hours_per_short_leave),
	)


def approved_requests(employee, date):
	"""Submitted short leaves for one employee on one day."""
	return frappe.get_all(
		REQUEST_DOCTYPE,
		filters={"employee": employee, "date": getdate(date), "docstatus": 1},
		fields=["name", "from_time", "to_time", "hours", "policy"],
		order_by="from_time",
	)
