"""Self-check for attendance-driven leave accrual.

Run: bench --site <site> execute frontier_hr.leave_accrual.test_leave_accrual.run

Rolls everything back, so it is safe against a site with real data.
"""

import frappe
from frappe.utils import add_days, flt, getdate

POLICY = "Attendance Leave Accrual Policy"
NAME = "_Test Accrual Policy"
SHIFT = "day"
LEAVE_TYPE = "_Test Earned Leave"


def _throws(fn):
	try:
		fn()
	except frappe.ValidationError:
		return True
	return False


def _employee():
	shift = frappe.db.get_value("Shift Type", SHIFT, ["name", "holiday_list"], as_dict=True)
	company = frappe.db.get_value("Company", {}, "name")
	doc = frappe.get_doc({
		"doctype": "Employee", "first_name": "_Accrual", "last_name": "Testcase",
		"gender": frappe.db.get_value("Gender", {}, "name"), "date_of_birth": "1995-01-01",
		"date_of_joining": "2025-01-01", "company": company, "status": "Active",
		"default_shift": SHIFT, "holiday_list": shift.holiday_list,
	}).insert(ignore_permissions=True)
	return doc.name, company


def _leave_type():
	if not frappe.db.exists("Leave Type", LEAVE_TYPE):
		frappe.get_doc({
			"doctype": "Leave Type", "leave_type_name": LEAVE_TYPE, "max_leaves_allowed": 0,
		}).insert(ignore_permissions=True)
	return LEAVE_TYPE


def _drop_policy():
	if frappe.db.exists(POLICY, NAME):
		frappe.delete_doc(POLICY, NAME, force=True, ignore_permissions=True)
	frappe.clear_document_cache(POLICY, NAME)


def _policy(company, **overrides):
	_drop_policy()
	values = {
		"doctype": POLICY, "policy_name": NAME, "company": company, "priority": 999,
		"days_required": 20, "leaves_granted": 1, "leave_type": _leave_type(),
		"reset_period": "Never", "carry_forward_remainder": 1, "carry_forward_counter": 1,
		"max_leaves_per_period": 0,
		"credits": [
			{"attendance_status": "Present", "credit": 1},
			{"attendance_status": "Half Day", "credit": 0.5},
			{"attendance_status": "Absent", "credit": 0},
		],
	}
	values.update(overrides)
	return frappe.get_doc(values).insert(ignore_permissions=True)


def _mark(employee, company, date, status="Present"):
	doc = frappe.get_doc({
		"doctype": "Attendance", "employee": employee, "attendance_date": date,
		"status": status, "company": company, "shift": SHIFT,
	}).insert(ignore_permissions=True)
	doc.submit()
	return doc


def _counter(employee):
	return flt(frappe.db.get_value("Employee", employee, "custom_attendance_credit_days"))


def _earned(employee):
	return flt(frappe.db.get_value("Employee", employee, "custom_attendance_leaves_earned"))


def _balance(employee, leave_type):
	total = frappe.db.sql(
		"""SELECT SUM(leaves) FROM `tabLeave Ledger Entry`
		   WHERE docstatus = 1 AND employee = %s AND leave_type = %s""",
		(employee, leave_type),
	)[0][0]
	return flt(total, 3)


# ---------------------------------------------------------------------------

def check_counter_increments():
	"""Present adds 1, Half Day adds 0.5, Absent adds nothing."""
	employee, company = _employee()
	_policy(company)

	_mark(employee, company, "2026-01-05")
	assert _counter(employee) == 1.0, _counter(employee)

	_mark(employee, company, "2026-01-06", status="Half Day")
	assert _counter(employee) == 1.5, _counter(employee)

	_mark(employee, company, "2026-01-07", status="Absent")
	assert _counter(employee) == 1.5, _counter(employee)

	# Re-submitting an unchanged row must not credit twice.
	doc = frappe.get_doc("Attendance", {"employee": employee, "attendance_date": "2026-01-05"})
	from frontier_hr.leave_accrual.overrides.attendance import accrue_on_submit
	accrue_on_submit(doc)
	assert _counter(employee) == 1.5, _counter(employee)


def check_grant_at_threshold():
	"""20 days earns one leave; a 20.5 counter grants once and keeps 0.5."""
	employee, company = _employee()
	_policy(company, days_required=20, leaves_granted=1)
	leave_type = _leave_type()

	date = getdate("2026-02-02")
	for i in range(20):
		_mark(employee, company, add_days(date, i))

	assert _counter(employee) == 0.0, _counter(employee)
	assert _earned(employee) == 1.0, _earned(employee)
	assert _balance(employee, leave_type) == 1.0, _balance(employee, leave_type)

	logs = frappe.get_all("Attendance Leave Grant Log",
		filters={"employee": employee}, fields=["leaves_granted", "counter_before", "counter_after"])
	assert len(logs) == 1 and logs[0].leaves_granted == 1.0, logs

	# Half a day more, then another 20: the 0.5 is carried, so the next grant
	# lands half a day early.
	_mark(employee, company, add_days(date, 20), status="Half Day")
	assert _counter(employee) == 0.5, _counter(employee)

	for i in range(21, 41):
		_mark(employee, company, add_days(date, i))
	assert _earned(employee) == 2.0, _earned(employee)
	assert _counter(employee) == 0.5, _counter(employee)  # 20.5 - 20
	assert _balance(employee, leave_type) == 2.0, _balance(employee, leave_type)


def check_remainder_dropped():
	"""With carry forward off, the leftover is discarded instead of kept."""
	employee, company = _employee()
	_policy(company, days_required=2, carry_forward_remainder=0)

	date = getdate("2026-03-02")
	_mark(employee, company, date)
	_mark(employee, company, add_days(date, 1))
	_mark(employee, company, add_days(date, 2), status="Half Day")
	# 2 days granted a leave and zeroed the counter; the half day follows it.
	assert _earned(employee) == 1.0, _earned(employee)
	assert _counter(employee) == 0.5, _counter(employee)


def check_period_reset():
	"""Monthly reset drops the counter at the month boundary unless carried."""
	employee, company = _employee()
	_policy(company, days_required=20, reset_period="Monthly", carry_forward_counter=0)

	_mark(employee, company, "2026-04-06")
	_mark(employee, company, "2026-04-07")
	assert _counter(employee) == 2.0, _counter(employee)

	# New month, counter not carried.
	_mark(employee, company, "2026-05-04")
	assert _counter(employee) == 1.0, _counter(employee)

	# Same policy but carrying the counter keeps it.
	employee2, _ = _employee()
	_policy(company, days_required=20, reset_period="Monthly", carry_forward_counter=1)
	_mark(employee2, company, "2026-04-06")
	_mark(employee2, company, "2026-04-07")
	_mark(employee2, company, "2026-05-04")
	assert _counter(employee2) == 3.0, _counter(employee2)


def check_period_cap():
	"""The per-period cap stops further grants; the counter keeps counting."""
	employee, company = _employee()
	_policy(company, days_required=2, max_leaves_per_period=1, reset_period="Monthly")

	date = getdate("2026-06-01")
	for i in range(6):
		_mark(employee, company, add_days(date, i))

	assert _earned(employee) == 1.0, _earned(employee)
	assert _counter(employee) == 4.0, _counter(employee)  # 6 earned, 2 spent on the one grant


def check_cancel_reverses():
	"""Cancelling gives back the credit, and claws back a grant it had earned."""
	employee, company = _employee()
	_policy(company, days_required=2)
	leave_type = _leave_type()

	date = getdate("2026-07-01")
	a = _mark(employee, company, date)
	b = _mark(employee, company, add_days(date, 1))
	assert _earned(employee) == 1.0 and _balance(employee, leave_type) == 1.0

	b.cancel()
	assert _counter(employee) == 1.0, _counter(employee)
	assert _earned(employee) == 0.0, _earned(employee)
	# The granted leave is taken back off the allocation too.
	assert _balance(employee, leave_type) == 0.0, _balance(employee, leave_type)

	a.cancel()
	assert _counter(employee) == 0.0, _counter(employee)


def check_validators():
	employee, company = _employee()
	assert _throws(lambda: _policy(company, days_required=0)), "zero threshold accepted"
	assert _throws(lambda: _policy(company, leaves_granted=0)), "zero grant accepted"
	assert _throws(lambda: _policy(company, credits=[
		{"attendance_status": "Present", "credit": 1},
		{"attendance_status": "Present", "credit": 1},
	])), "duplicate status accepted"
	assert _throws(lambda: _policy(company, credits=[
		{"attendance_status": "Present", "credit": 0},
	])), "a table that can never move the counter was accepted"


def check_backfill_after_newer_period():
	"""Crediting an OLDER day after a newer one must not wipe the period total.

	`earned` used to be a field that any period change reset. A backfill walks
	old days after the live run has already stored a newer period, so stepping
	back zeroed it — which silently disabled the per-period cap and the
	claw-back guard, leaving granted leave that nothing could take back.
	"""
	employee, company = _employee()
	_policy(company, days_required=2, reset_period="Monthly")
	leave_type = _leave_type()

	# A newer month first, exactly as the live run then backfill did.
	_mark(employee, company, "2026-09-07")
	_mark(employee, company, "2026-09-08")
	assert _earned(employee) == 1.0, _earned(employee)
	assert _balance(employee, leave_type) == 1.0, _balance(employee, leave_type)

	# Now an older month. The stored period must not step backwards, and the
	# earned total must still reflect what was granted.
	_mark(employee, company, "2026-08-10")
	period = frappe.db.get_value("Employee", employee, "custom_attendance_credit_period")
	assert period == "2026-09", period

	# The backfilled day keeps the grant earned: 09-07 + 08-10 still make the
	# two days it was given for, so cancelling 09-08 alone takes nothing back.
	frappe.get_doc("Attendance", {"employee": employee, "attendance_date": "2026-09-08"}).cancel()
	assert _balance(employee, leave_type) == 1.0, _balance(employee, leave_type)

	# Dropping below the threshold must claw it back, which only works while
	# the earned total is still visible — the bug this check exists for.
	frappe.get_doc("Attendance", {"employee": employee, "attendance_date": "2026-08-10"}).cancel()
	assert _balance(employee, leave_type) == 0.0, _balance(employee, leave_type)
	assert _counter(employee) == 1.0, _counter(employee)  # only 09-07 still credits


def run():
	check_counter_increments()
	check_grant_at_threshold()
	check_remainder_dropped()
	check_period_reset()
	check_period_cap()
	check_cancel_reverses()
	check_backfill_after_newer_period()
	check_validators()
	frappe.db.rollback()
	print("leave accrual self-check OK (increment, grant, remainder, reset, cap, reversal, backfill order, validators)")
