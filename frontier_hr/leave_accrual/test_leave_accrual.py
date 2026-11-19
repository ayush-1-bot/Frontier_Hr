"""Self-check for attendance-driven leave accrual.

Run: bench --site <site> execute frontier_hr.leave_accrual.test_leave_accrual.run

Rolls everything back, so it is safe against a site with real data.
"""

import frappe
from frappe.utils import add_days, flt, getdate, today

POLICY = "Attendance Leave Accrual Policy"
GRANT_LOG = "Attendance Leave Grant Log"
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


def _mark(employee, company, date, status="Present", shift=SHIFT):
	doc = frappe.get_doc({
		"doctype": "Attendance", "employee": employee, "attendance_date": date,
		"status": status, "company": company, "shift": shift,
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


def check_non_working_days():
	"""Holidays and weekly offs earn credit even though they have no Attendance."""
	from frontier_hr.leave_accrual.tasks import credit_non_working_days

	employee, company = _employee()
	holiday_list = frappe.db.get_value("Employee", employee, "holiday_list")
	if not holiday_list:
		print("   (skipped: shift has no holiday list)")
		return

	# A public holiday and a weekly off that nothing else uses, both in the
	# PAST: credit_non_working_days refuses to walk past today, and a fixed
	# future date would stop working the moment that date arrived.
	off = add_days(getdate(today()), -2)
	hol = add_days(off, -1)
	while frappe.db.exists("Holiday", {"parent": holiday_list, "holiday_date": hol}) or \
			frappe.db.exists("Holiday", {"parent": holiday_list, "holiday_date": off}):
		off = add_days(off, -2)
		hol = add_days(off, -1)
	frappe.db.set_value("Employee", employee, "custom_attendance_credit_upto",
	                    add_days(hol, -1), update_modified=False)
	for date, weekly in ((hol, 0), (off, 1)):
		if not frappe.db.exists("Holiday", {"parent": holiday_list, "holiday_date": date}):
			parent = frappe.get_doc("Holiday List", holiday_list)
			parent.append("holidays", {"holiday_date": date, "description": "_test",
			                           "weekly_off": weekly})
			parent.flags.ignore_validate = True
			parent.save(ignore_permissions=True)

	policy = _policy(company, days_required=20, credits=[
		{"attendance_status": "Present", "credit": 1},
		{"attendance_status": "Holiday", "credit": 1},
		{"attendance_status": "Weekly Off", "credit": 0.5},
	])
	assert policy.credit_holidays == 1, "the holiday switch must default to on"
	assert policy.credit_weekly_offs == 1, "the weekly off switch must default to on"

	def rewind():
		frappe.db.set_value("Employee", employee, {
			"custom_attendance_credit_days": 0,
			"custom_attendance_credit_upto": add_days(hol, -1),
		}, update_modified=False)

	def switches(holidays, weekly_offs):
		policy.db_set("credit_holidays", holidays)
		policy.db_set("credit_weekly_offs", weekly_offs)
		frappe.clear_document_cache(POLICY, policy.name)

	# Both off: nothing is credited however the credit table is filled.
	switches(0, 0)
	credit_non_working_days(off, employee=employee)
	assert _counter(employee) == 0, _counter(employee)

	# Holidays only -- the weekly off must stay out.
	rewind()
	switches(1, 0)
	credit_non_working_days(off, employee=employee)
	assert _counter(employee) == 1.0, f"holiday-only gave {_counter(employee)}, wanted 1.0"

	# Weekly offs only -- the public holiday must stay out.
	rewind()
	switches(0, 1)
	credit_non_working_days(off, employee=employee)
	assert _counter(employee) == 0.5, f"weekly-off-only gave {_counter(employee)}, wanted 0.5"

	rewind()
	switches(1, 1)
	credit_non_working_days(off, employee=employee)
	assert _counter(employee) == 1.5, _counter(employee)  # 1 holiday + 0.5 weekly off

	# Running again must not credit the same days twice.
	credit_non_working_days(off, employee=employee)
	assert _counter(employee) == 1.5, _counter(employee)

	# A day that already has an Attendance row is left to the submit hook.
	before = _counter(employee)
	# A later day with Attendance still credits once, via the submit hook.
	_mark(employee, company, add_days(off, 1))
	assert _counter(employee) == before + 1.0, _counter(employee)

	# The job must leave the commit to its caller, or a test's rollback cannot
	# undo it and the watermark lands on every employee on the site.
	import inspect

	from frontier_hr.leave_accrual import tasks

	assert "db.commit" not in inspect.getsource(tasks.credit_non_working_days), \
		"credit_non_working_days commits; a caller's rollback can no longer undo it"

	# A date past today is clamped, so a typo cannot park the watermark in the
	# future and make every later run skip the days in between.
	credit_non_working_days(add_days(getdate(today()), 30), employee=employee)
	assert getdate(frappe.db.get_value(
		"Employee", employee, "custom_attendance_credit_upto"
	)) <= getdate(today()), "watermark moved into the future"


def _user(role):
	"""A throwaway user with one HR role, for the permlevel check."""
	email = f"_accrual_{role.lower().replace(' ', '_')}@example.com"
	if frappe.db.exists("User", email):
		frappe.delete_doc("User", email, force=True, ignore_permissions=True)
	doc = frappe.get_doc({
		"doctype": "User", "email": email, "first_name": "Accrual", "last_name": role,
		"send_welcome_email": 0, "roles": [{"role": role}],
	}).insert(ignore_permissions=True)
	return doc.name


def check_policy_field_permlevel():
	"""Only HR Manager may reassign the accrual policy on an Employee.

	The field sits at permlevel 1. Frappe silently reverts a higher-permlevel
	field a user cannot write, so the check is that the value did NOT change --
	not that the save threw.
	"""
	from frontier_hr.install import ensure_accrual_permlevel_perms

	ensure_accrual_permlevel_perms()
	assert frappe.db.get_value(
		"Custom Field", {"dt": "Employee", "fieldname": "custom_attendance_accrual_policy"},
		"permlevel",
	) == 1, "accrual policy field is not at permlevel 1"

	employee, company = _employee()
	policy = _policy(company)
	frappe.db.set_value("Employee", employee, "custom_attendance_accrual_policy", policy.name)

	hr_user, hr_manager = _user("HR User"), _user("HR Manager")
	original = frappe.session.user
	try:
		# HR User writes Employee at level 0 but must not touch this field.
		frappe.set_user(hr_user)
		doc = frappe.get_doc("Employee", employee)
		doc.custom_attendance_accrual_policy = None
		doc.save()
		assert frappe.db.get_value(
			"Employee", employee, "custom_attendance_accrual_policy"
		) == policy.name, "HR User cleared the accrual policy"

		# HR Manager may.
		frappe.set_user(hr_manager)
		doc = frappe.get_doc("Employee", employee)
		doc.custom_attendance_accrual_policy = None
		doc.save()
		assert not frappe.db.get_value(
			"Employee", employee, "custom_attendance_accrual_policy"
		), "HR Manager could not clear the accrual policy"
	finally:
		frappe.set_user(original)


def check_grant_across_month_boundary():
	"""A grant triggered by an older day still counts in the current period.

	Bulk Mark Attendance submits newest first. The counter's period only moves
	forward, so a day from the previous month is credited while the period
	says this month -- and the grant log's grant_date is that older date. If
	`earned` were read from grant_date's own month it would come back 0, which
	silently disables the per-period cap and the claw-back guard.
	"""
	employee, company = _employee()
	policy = _policy(company, days_required=20, reset_period="Monthly",
	                 carry_forward_remainder=1)

	# 21 days ending today, submitted NEWEST first, so the 21st row is the
	# oldest date and falls in the previous month.
	dates = [add_days(getdate(today()), -i) for i in range(21)]
	assert dates[0].month != dates[-1].month, "pick a window that crosses a month"
	for date in dates:
		_mark(employee, company, date)

	period = frappe.db.get_value("Employee", employee, "custom_attendance_credit_period")
	assert period == dates[0].strftime("%Y-%m"), period

	log = frappe.get_all(
		GRANT_LOG, filters={"employee": employee},
		fields=["grant_date", "period", "leaves_granted", "leave_allocation"],
	)
	assert len(log) == 1, log
	assert log[0].period == period, f"grant charged to {log[0].period}, counter is in {period}"
	assert log[0].leave_allocation, "no allocation on the grant"

	# 21 days, one leave at 20, one day carried.
	assert _counter(employee) == 1.0, _counter(employee)
	assert _earned(employee) == 1.0, f"earned reads {_earned(employee)}, expected 1.0"


def check_attendance_request_promotes_half_day():
	"""A Half Day promoted to Present by an Attendance Request re-credits.

	hrms rewrites the status with db_set, which runs no hooks, so without the
	Attendance Request hook the day keeps the 0.5 it earned as a half day.
	"""
	employee, company = _employee()
	_policy(company, days_required=20)

	date = add_days(getdate(today()), -1)
	_mark(employee, company, date, status="Half Day")
	assert _counter(employee) == 0.5, _counter(employee)

	frappe.get_doc({
		"doctype": "Attendance Request", "employee": employee, "company": company,
		"from_date": date, "to_date": date, "reason": "On Duty", "half_day": 0,
		# hrms matches the existing Attendance on shift too, so a request
		# without one inserts a second row and hits DuplicateAttendanceError.
		"shift": SHIFT,
	}).insert(ignore_permissions=True).submit()

	attendance = frappe.db.get_value(
		"Attendance", {"employee": employee, "attendance_date": date, "docstatus": 1},
		["name", "status", "custom_accrual_credit_days"], as_dict=True,
	)
	assert attendance.status == "Present", attendance
	assert attendance.custom_accrual_credit_days == 1.0, attendance
	assert _counter(employee) == 1.0, f"counter stayed at {_counter(employee)}"


def check_leave_application_resyncs():
	"""A Half Day turned into leave loses its credit, and gets it back on cancel.

	hrms rewrites the status with db_set and later cancels the row with a raw
	docstatus update -- neither runs a hook, so without the Leave Application
	resync the day keeps the 0.5 it earned as a half day.
	"""
	employee, company = _employee()
	_policy(company, days_required=20)
	leave_type = _leave_type()

	date = add_days(getdate(today()), -4)
	allocation = frappe.get_doc({
		"doctype": "Leave Allocation", "employee": employee, "leave_type": leave_type,
		"from_date": add_days(date, -30), "to_date": add_days(date, 30),
		"new_leaves_allocated": 10, "company": company,
	}).insert(ignore_permissions=True)
	allocation.submit()

	# half_day_status "Absent" is what auto-attendance writes, and is also what
	# lets hrms accept a leave over an already-marked day.
	attendance = _mark(employee, company, date, status="Half Day")
	attendance.db_set("half_day_status", "Absent")
	assert _counter(employee) == 0.5, _counter(employee)

	application = frappe.get_doc({
		"doctype": "Leave Application", "employee": employee, "leave_type": leave_type,
		"from_date": date, "to_date": date, "company": company, "status": "Approved",
		"leave_approver": frappe.session.user,
	}).insert(ignore_permissions=True)
	application.submit()

	row = frappe.db.get_value("Attendance", attendance.name,
	                          ["status", "docstatus", "custom_accrual_credit_days"], as_dict=True)
	assert row.status == "On Leave", row
	assert row.custom_accrual_credit_days == 0, row
	assert _counter(employee) == 0, f"On Leave still earns {_counter(employee)}"

	# Cancelling the leave cancels the Attendance in raw SQL, firing no
	# on_cancel. There is nothing left to give back, so the counter holds at 0.
	application.cancel()
	assert _counter(employee) == 0, _counter(employee)


def _lwp_leave_type():
	name = "_Test LWP Leave"
	if not frappe.db.exists("Leave Type", name):
		frappe.get_doc({
			"doctype": "Leave Type", "leave_type_name": name, "is_lwp": 1,
			"max_leaves_allowed": 0,
		}).insert(ignore_permissions=True)
	return name


def check_paid_leave_and_work_from_home():
	"""On Leave earns only on paid leave; Work From Home earns its own row.

	The paid rule is a policy switch, so a site that does credit Leave Without
	Pay can still do it. Work From Home needs no switch: the credit table has a
	row of its own, set to 1 to rate it as a full present day.
	"""
	from frontier_hr.leave_accrual.overrides.attendance import _credit_for

	employee, company = _employee()
	paid, lwp = _leave_type(), _lwp_leave_type()
	policy = _policy(company, credits=[
		{"attendance_status": "Present", "credit": 1},
		{"attendance_status": "Half Day", "credit": 0.5, "half_day_credit": 1},
		{"attendance_status": "On Leave", "credit": 1},
		{"attendance_status": "Work From Home", "credit": 1},
	])
	assert policy.credit_paid_leave_only == 1, "paid leave only should default on"

	def credit(**row):
		return _credit_for(frappe._dict(row), policy)

	# --- On Leave --------------------------------------------------------
	assert credit(status="On Leave", leave_type=paid) == 1.0
	assert credit(status="On Leave", leave_type=lwp) == 0.0, "LWP earned credit"
	# The half day bonus is for a paid other half, so it follows the same rule.
	assert credit(status="Half Day", half_day_status="Present", leave_type=paid) == 1.0
	assert credit(status="Half Day", half_day_status="Present", leave_type=lwp) == 0.5
	# A plain half day with no leave at all keeps the plain rate.
	assert credit(status="Half Day", half_day_status="Absent") == 0.5

	policy.credit_paid_leave_only = 0
	assert credit(status="On Leave", leave_type=lwp) == 1.0
	assert credit(status="Half Day", half_day_status="Present", leave_type=lwp) == 1.0
	policy.credit_paid_leave_only = 1

	# --- Work From Home --------------------------------------------------
	# Straight off its own row, at whatever the table says -- 1 here, so the
	# day counts the same as being in the office.
	assert credit(status="Work From Home") == 1.0, credit(status="Work From Home")

	# And the whole way through a real Attendance, not just the lookup.
	_mark(employee, company, add_days(getdate(today()), -2), status="Work From Home")
	assert _counter(employee) == 1.0, _counter(employee)


def check_multiple_shift_scope():
	"""A policy can pin several shifts, and the old single Link still works.

	Runs on a shift the SITE's own policies do not pin, so an unmatched test
	policy means no policy matches at all -- otherwise a real policy would
	quietly take over and the "no credit" case would still credit.
	"""
	employee, company = _employee()
	other = frappe.db.get_value("Shift Type", {"name": ["!=", SHIFT]}, "name")
	assert other, "this check needs a second Shift Type on the site"

	# --- pinned to another shift: no match, no credit -------------------
	_policy(company, shift_types=[{"shift_type": SHIFT}])
	_mark(employee, company, "2026-03-02", shift=other)
	assert _counter(employee) == 0.0, _counter(employee)

	# --- both shifts listed: matches ------------------------------------
	_policy(company, shift_types=[{"shift_type": SHIFT}, {"shift_type": other}])
	_mark(employee, company, "2026-03-03", shift=other)
	assert _counter(employee) == 1.0, _counter(employee)

	# --- empty table: every shift ---------------------------------------
	_policy(company)
	_mark(employee, company, "2026-03-04", shift=other)
	assert _counter(employee) == 2.0, _counter(employee)

	# --- the old single Link is folded into the table on save -----------
	policy = _policy(company, shift_type=other)
	assert not policy.shift_type, f"legacy Link left set: {policy.shift_type}"
	assert [row.shift_type for row in policy.shift_types] == [other], policy.shift_types
	_mark(employee, company, "2026-03-05", shift=other)
	assert _counter(employee) == 3.0, _counter(employee)

	# --- a shift listed twice must not count double ---------------------
	policy = _policy(company, shift_types=[{"shift_type": other}, {"shift_type": other}])
	assert len(policy.shift_types) == 1, policy.shift_types


def run():
	check_counter_increments()
	check_grant_at_threshold()
	check_remainder_dropped()
	check_period_reset()
	check_period_cap()
	check_cancel_reverses()
	check_backfill_after_newer_period()
	check_non_working_days()
	check_validators()
	check_policy_field_permlevel()
	check_grant_across_month_boundary()
	check_attendance_request_promotes_half_day()
	check_leave_application_resyncs()
	check_paid_leave_and_work_from_home()
	check_multiple_shift_scope()
	frappe.db.rollback()
	print("leave accrual self-check OK (increment, grant, remainder, reset, cap, reversal, backfill order, non-working days, validators, policy permlevel, month boundary, attendance request, leave application, paid leave only, work from home, multiple shifts)")
