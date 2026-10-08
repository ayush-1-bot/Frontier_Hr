"""Self-check for the Late Coming and Early Out Deduction Policy.

Run: bench --site <site> execute frontier_hr.deduction_policy.test_deduction_policy.run

Rolls everything back, so it is safe against a site with real data.
"""

import frappe
from frappe.utils import flt

from frontier_hr.deduction_policy.overrides.attendance import (
	_early_minutes,
	_late_minutes,
	_leave_balance,
	_slab_minutes,
	post_leave_ledger,
)

POLICY = "Late Coming and Early Out Deduction Policy"
NAME = "_Test Deduction Policy"
SHIFT = "day"


def _test_employee():
	"""A throwaway employee of this run's own.

	Never a seeded one: a site carries real Leave Allocations, and a second
	allocation overlapping the same period is refused, so the leave checks
	would fail on whatever balances happened to be there. Rolled back with
	everything else.
	"""
	shift = frappe.db.get_value("Shift Type", SHIFT, ["name", "holiday_list"], as_dict=True)
	company = frappe.db.get_value("Company", {}, "name")
	doc = frappe.get_doc({
		"doctype": "Employee",
		"first_name": "_Deduction",
		"last_name": "Testcase",
		"gender": frappe.db.get_value("Gender", {}, "name"),
		"date_of_birth": "1995-01-01",
		"date_of_joining": "2025-01-01",
		"company": company,
		"status": "Active",
		"default_shift": SHIFT,
		"holiday_list": shift.holiday_list,
	}).insert(ignore_permissions=True)
	return doc.name, company


def _drop_policy():
	"""frappe.db.delete bypasses the document layer and leaves the row in the
	document cache, which _resolve_policy reads through get_cached_doc — the
	next check would then run against the policy this one just deleted."""
	if frappe.db.exists(POLICY, NAME):
		frappe.delete_doc(POLICY, NAME, force=True, ignore_permissions=True)
	frappe.clear_document_cache(POLICY, NAME)


def _throws(fn):
	try:
		fn()
	except frappe.ValidationError:
		return True
	return False


def _policy(slabs=None, priority=None):
	company = frappe.db.get_value("Company", {}, "name")
	leave_type = frappe.db.get_value("Leave Type", {}, "name")
	doc = frappe.get_doc(
		{
			"doctype": POLICY,
			"policy_name": NAME,
			"company": company,
			"late_grace_minutes": 10,
			"early_grace_minutes": 5,
			"allowance_period": "Monthly",
			"allowed_late_count": 3,
			"deduction_slabs": slabs
			if slabs is not None
			else [
				{"applies_to": "Both", "from_minutes": 1, "to_minutes": 30, "deduct_minutes": 30},
				{"applies_to": "Both", "from_minutes": 31, "to_minutes": 60, "deduct_minutes": 60},
				{"applies_to": "Both", "from_minutes": 61, "to_minutes": 0, "deduct_minutes": 120},
			],
			"deduction_priority": priority
			if priority is not None
			else [
				{"source": "Leave", "leave_type": leave_type, "hours_per_unit": 2, "days_per_unit": 0.5},
				{"source": "Working Hours"},
			],
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def check_measurement():
	"""Step 2 — minutes late and early, net of grace, on a day and a night shift."""
	day = frappe._dict(start_time="09:30:00", end_time="18:30:00")
	policy = frappe._dict(late_grace_minutes=10, early_grace_minutes=5)

	def att(in_t=None, out_t=None, date="2026-09-01", out_date=None):
		return frappe._dict(
			attendance_date=date,
			in_time=f"{date} {in_t}" if in_t else None,
			out_time=f"{out_date or date} {out_t}" if out_t else None,
		)

	# --- late coming, 10 minute grace ------------------------------------
	assert _late_minutes(att("09:30:00"), day, policy) == 0     # on the dot
	assert _late_minutes(att("09:20:00"), day, policy) == 0     # early: never negative
	assert _late_minutes(att("09:35:00"), day, policy) == 0     # 5 late, inside grace
	assert _late_minutes(att("09:40:00"), day, policy) == 0     # 10 late, grace is inclusive
	assert _late_minutes(att("09:41:00"), day, policy) == 1     # first minute past grace
	assert _late_minutes(att("09:50:00"), day, policy) == 10    # 20 late
	assert _late_minutes(att("10:25:00"), day, policy) == 45    # 55 late
	assert _late_minutes(att("11:00:00"), day, policy) == 80    # 90 late
	assert _late_minutes(att(None), day, policy) == 0           # no punch in

	# --- early out, 5 minute grace ---------------------------------------
	assert _early_minutes(att(out_t="18:30:00"), day, policy) == 0    # on the dot
	assert _early_minutes(att(out_t="18:35:00"), day, policy) == 0    # stayed late
	assert _early_minutes(att(out_t="18:27:00"), day, policy) == 0    # 3 early, inside grace
	assert _early_minutes(att(out_t="18:23:00"), day, policy) == 2    # 7 early
	assert _early_minutes(att(out_t="17:30:00"), day, policy) == 55   # 60 early
	assert _early_minutes(att(out_t="16:00:00"), day, policy) == 145  # 150 early
	assert _early_minutes(att(out_t=None), day, policy) == 0          # no punch out

	# --- night shift: the end rolls onto the next calendar day ------------
	night = frappe._dict(start_time="22:00:00", end_time="06:00:00")
	assert _late_minutes(att("22:30:00"), night, policy) == 20
	assert _early_minutes(att(out_t="05:30:00", out_date="2026-09-02"), night, policy) == 25
	# Punched out after the shift ended, still on the next day: not early.
	assert _early_minutes(att(out_t="06:10:00", out_date="2026-09-02"), night, policy) == 0

	# --- grace of zero means every minute counts --------------------------
	strict = frappe._dict(late_grace_minutes=0, early_grace_minutes=0)
	assert _late_minutes(att("09:31:00"), day, strict) == 1
	assert _early_minutes(att(out_t="18:29:00"), day, strict) == 1


def check_allowance_and_walk():
	"""Step 3 — the free allowance, and the Working Hours end of the priority
	walk, driven through a real Attendance save so the hook order is exercised
	too. Dates sit in a clean future month so nothing collides with real rows.
	"""
	employee, company = _test_employee()

	_drop_policy()
	policy = frappe.get_doc({
		"doctype": POLICY,
		"policy_name": NAME,
		"company": company,
		"shift_type": SHIFT,
		# Sites carry real policies too; outrank them so the checks below are
		# testing this policy rather than whatever else happens to match.
		"priority": 999,
		"late_grace_minutes": 10,
		"early_grace_minutes": 5,
		"allowance_period": "Monthly",
		"allowed_late_count": 2,
		"deduction_slabs": [
			{"applies_to": "Both", "from_minutes": 1, "to_minutes": 30, "deduct_minutes": 30},
			{"applies_to": "Both", "from_minutes": 31, "to_minutes": 60, "deduct_minutes": 60},
			{"applies_to": "Both", "from_minutes": 61, "to_minutes": 0, "deduct_minutes": 120},
		],
		# Working Hours only for now; the leave rows are step 4.
		"deduction_priority": [{"source": "Working Hours"}],
	}).insert(ignore_permissions=True)

	def attendance(date, in_time, out_time="18:30:00", hours=8.5):
		return frappe.get_doc({
			"doctype": "Attendance", "employee": employee, "attendance_date": date,
			"status": "Present", "company": company, "shift": SHIFT,
			"in_time": f"{date} {in_time}", "out_time": f"{date} {out_time}",
			"working_hours": hours,
		}).insert(ignore_permissions=True)

	# Shift starts 09:30, grace 10 minutes, 2 allowed times.
	# 09:38 is 8 late: inside the grace, so it is forgiven and uses a time up.
	d1 = attendance("2026-10-05", "09:38:00")
	d2 = attendance("2026-10-06", "09:35:00")
	assert (d1.custom_late_coming_raw_minutes, d1.custom_late_coming_minutes) == (8, 0)
	assert (d2.custom_late_coming_raw_minutes, d2.custom_late_coming_minutes) == (5, 0)
	assert d1.custom_deduction_minutes == 0 and d2.custom_deduction_minutes == 0

	# Third time inside the grace: the allowed times are gone, so the grace no
	# longer applies and even 3 minutes late is counted and charged.
	d3 = attendance("2026-10-07", "09:33:00")
	assert d3.custom_late_coming_raw_minutes == 3, d3.custom_late_coming_raw_minutes
	assert d3.custom_late_coming_minutes == 3, d3.custom_late_coming_minutes
	assert d3.custom_deduction_minutes == 30, d3.custom_deduction_minutes

	# Even one minute now counts.
	d4 = attendance("2026-10-08", "09:31:00")
	assert d4.custom_late_coming_minutes == 1, d4.custom_late_coming_minutes
	assert d4.custom_deduction_minutes == 30, d4.custom_deduction_minutes

	# The policy is stamped on every row it judged.
	for doc in (d1, d2, d3, d4):
		assert doc.custom_deduction_policy == NAME, doc.attendance_date

	# Re-saving must not deduct twice.
	before = d4.custom_base_hours
	d4.save(ignore_permissions=True)
	assert d4.custom_base_hours == before, (before, d4.custom_base_hours)

	made = [d1, d2, d3, d4]

	# A day BEYOND the grace is charged at once and never uses a time up. Fresh
	# month, so the quota is untouched.
	big = attendance("2026-11-09", "10:25:00")
	assert big.custom_late_coming_raw_minutes == 55, big.custom_late_coming_raw_minutes
	assert big.custom_late_coming_minutes == 45, big.custom_late_coming_minutes  # grace still shaves it
	assert big.custom_deduction_minutes == 60, big.custom_deduction_minutes
	# ...and the grace is still available afterwards, so a small day is forgiven.
	small = attendance("2026-11-10", "09:36:00")
	assert small.custom_late_coming_minutes == 0, small.custom_late_coming_minutes
	assert small.custom_deduction_minutes == 0, small.custom_deduction_minutes

	# Re-scoped to another shift, this policy must stop matching the row. Any
	# real policy on the site may then take over, which is correct behaviour —
	# so the check is that THIS policy stepped aside, not that none matched.
	if frappe.db.exists("Shift Type", "testing"):
		policy.shift_type = "testing"
		policy.save(ignore_permissions=True)
		made[3].save(ignore_permissions=True)
		assert made[3].custom_deduction_policy != NAME, made[3].custom_deduction_policy


def _leave_type(name, **kwargs):
	if frappe.db.exists("Leave Type", name):
		return name
	frappe.get_doc(dict(doctype="Leave Type", leave_type_name=name, **kwargs)).insert(
		ignore_permissions=True
	)
	return name


def _allocate(employee, leave_type, days, company):
	"""Submitted Leave Allocation, which is what puts leaves on the ledger."""
	frappe.get_doc({
		"doctype": "Leave Allocation",
		"employee": employee,
		"leave_type": leave_type,
		"from_date": "2026-01-01",
		"to_date": "2026-12-31",
		"new_leaves_allocated": days,
		"company": company,
		"carry_forward": 0,
	}).insert(ignore_permissions=True).submit()


def _balance(employee, leave_type, date):
	return round(_leave_balance(frappe._dict(employee=employee, attendance_date=date, name=None), leave_type), 4)


def check_leave_walk():
	"""Step 4 — a leave row is drained only as far as the balance stretches and
	the rest falls to the next row, then the debit reaches the ledger on submit
	and comes back on cancel."""
	employee, company = _test_employee()

	short = _leave_type("Short Leave", max_leaves_allowed=10)
	casual = _leave_type("Casual Leave")

	# Short Leave: 1 unit = 2 hours = 0.5 day, so 0.25 day buys 60 minutes.
	# Casual Leave: 1 unit = 8 hours = 1 day.
	_allocate(employee, short, 0.25, company)
	_allocate(employee, casual, 1, company)

	_drop_policy()
	frappe.get_doc({
		"doctype": POLICY,
		"policy_name": NAME,
		"company": company,
		"shift_type": SHIFT,
		"priority": 999,
		"late_grace_minutes": 10,
		"allowance_period": "Monthly",
		"allowed_late_count": 0,
		"deduction_slabs": [
			{"applies_to": "Both", "from_minutes": 1, "to_minutes": 0, "deduct_minutes": 120},
		],
		"deduction_priority": [
			{"source": "Leave", "leave_type": short, "hours_per_unit": 2, "days_per_unit": 0.5},
			{"source": "Leave", "leave_type": casual, "hours_per_unit": 8, "days_per_unit": 1},
			{"source": "Working Hours"},
		],
	}).insert(ignore_permissions=True)

	date = "2026-11-02"
	doc = frappe.get_doc({
		"doctype": "Attendance",
		"employee": employee,
		"attendance_date": date,
		"status": "Present",
		"company": company,
		"shift": SHIFT,
		"in_time": f"{date} 11:00:00",
		"out_time": f"{date} 18:30:00",
		"working_hours": 7.0,
	}).insert(ignore_permissions=True)

	assert doc.custom_deduction_minutes == 120, doc.custom_deduction_minutes

	rows = {r.source if r.source == "Working Hours" else r.leave_type: r for r in doc.custom_deduction_breakup}
	# Short Leave holds only 0.25 day = 60 minutes, so it covers half and stops.
	assert rows[short].leave_days == 0.25, rows[short].leave_days
	assert rows[short].minutes == 60, rows[short].minutes
	# The other 60 minutes falls to Casual: 1 hour of an 8 hour day = 0.125.
	assert rows[casual].leave_days == 0.125, rows[casual].leave_days
	assert rows[casual].minutes == 60, rows[casual].minutes
	# Both leave rows covered it, so Working Hours is never reached.
	assert "Working Hours" not in rows, rows.keys()
	assert doc.custom_deducted_leave_days == 0.375, doc.custom_deducted_leave_days
	assert doc.custom_deducted_hours == 0, doc.custom_deducted_hours

	# --- nothing reaches the ledger before submit ------------------------
	ledger = {"transaction_type": "Attendance", "transaction_name": doc.name, "docstatus": 1}
	assert frappe.db.count("Leave Ledger Entry", ledger) == 0

	def net():
		total = frappe.db.sql(
			"""SELECT SUM(leaves) FROM `tabLeave Ledger Entry`
			   WHERE transaction_type = 'Attendance' AND transaction_name = %s AND docstatus = 1""",
			doc.name,
		)[0][0]
		return round(float(total or 0), 4)

	before = _balance(employee, short, date)
	doc.submit()
	assert frappe.db.count("Leave Ledger Entry", ledger) == 2, "leave not debited on submit"
	assert net() == -0.375, net()
	assert _balance(employee, short, date) == before - 0.25, _balance(employee, short, date)

	# Submitting again must not debit twice.
	post_leave_ledger(doc)
	assert net() == -0.375, net()

	# --- and comes back on cancel ----------------------------------------
	# The rows are deleted, the way hrms reverses a Leave Application.
	doc.cancel()
	assert net() == 0, net()
	assert frappe.db.count("Leave Ledger Entry", ledger) == 0, frappe.db.count("Leave Ledger Entry", ledger)
	assert _balance(employee, short, date) == before, _balance(employee, short, date)


def check_report_only():
	"""Report Only works everything out but costs the employee nothing."""
	employee, company = _test_employee()
	short = _leave_type("Short Leave", max_leaves_allowed=10)
	_allocate(employee, short, 1, company)

	_drop_policy()
	frappe.get_doc({
		"doctype": POLICY, "policy_name": NAME, "company": company, "shift_type": SHIFT,
		"priority": 999, "report_only": 1,
		"late_grace_minutes": 10, "allowance_period": "Monthly", "allowed_late_count": 0,
		"deduction_slabs": [
			{"applies_to": "Both", "from_minutes": 1, "to_minutes": 0, "deduct_minutes": 120},
		],
		"deduction_priority": [
			{"source": "Leave", "leave_type": short, "hours_per_unit": 2, "days_per_unit": 0.5},
			{"source": "Working Hours"},
		],
	}).insert(ignore_permissions=True)

	date = "2026-12-07"
	doc = frappe.get_doc({
		"doctype": "Attendance", "employee": employee, "attendance_date": date,
		"status": "Present", "company": company, "shift": SHIFT,
		"in_time": f"{date} 11:00:00", "out_time": f"{date} 18:30:00", "working_hours": 7.0,
	}).insert(ignore_permissions=True)

	# The figures are all worked out and recorded...
	assert doc.custom_deduction_report_only == 1
	assert doc.custom_deduction_minutes == 120, doc.custom_deduction_minutes
	assert doc.custom_deducted_leave_days == 0.5, doc.custom_deducted_leave_days
	assert len(doc.custom_deduction_breakup) == 1, doc.custom_deduction_breakup

	# ...but the payable hours are untouched.
	base_before = flt(doc.custom_base_hours)
	balance_before = _balance(employee, short, date)

	doc.submit()
	assert flt(doc.custom_base_hours) == base_before, (base_before, doc.custom_base_hours)
	assert frappe.db.count("Leave Ledger Entry", {
		"transaction_type": "Attendance", "transaction_name": doc.name, "docstatus": 1
	}) == 0, "report-only debited leave"
	assert _balance(employee, short, date) == balance_before

	# Switching it off makes the same day bite.
	policy = frappe.get_doc(POLICY, NAME)
	policy.report_only = 0
	policy.save(ignore_permissions=True)

	date2 = "2026-12-08"
	real = frappe.get_doc({
		"doctype": "Attendance", "employee": employee, "attendance_date": date2,
		"status": "Present", "company": company, "shift": SHIFT,
		"in_time": f"{date2} 11:00:00", "out_time": f"{date2} 18:30:00", "working_hours": 7.0,
	}).insert(ignore_permissions=True)
	assert real.custom_deduction_report_only == 0
	assert real.custom_deducted_leave_days == 0.5, real.custom_deducted_leave_days
	real.submit()
	assert _balance(employee, short, date2) == balance_before - 0.5, _balance(employee, short, date2)


def check_actual_minutes():
	"""Actual Late Minutes deducts exactly what was lost, ignoring the ranges."""
	employee, company = _test_employee()

	_drop_policy()
	frappe.get_doc({
		"doctype": POLICY, "policy_name": NAME, "company": company, "shift_type": SHIFT,
		"priority": 999, "deduction_basis": "Actual Late Minutes",
		"late_grace_minutes": 10, "early_grace_minutes": 5,
		"allowance_period": "Monthly", "allowed_late_count": 0,
		# Deliberately left in place: the ranges must be ignored, not applied.
		"deduction_slabs": [
			{"applies_to": "Both", "from_minutes": 1, "to_minutes": 0, "deduct_minutes": 999},
		],
		"deduction_priority": [{"source": "Working Hours"}],
	}).insert(ignore_permissions=True)

	def day(date, in_time, out_time="18:30:00"):
		return frappe.get_doc({
			"doctype": "Attendance", "employee": employee, "attendance_date": date,
			"status": "Present", "company": company, "shift": SHIFT,
			"in_time": f"{date} {in_time}", "out_time": f"{date} {out_time}",
			"working_hours": 8.0,
		}).insert(ignore_permissions=True)

	# 09:53 is 23 late, less 10 grace = 13 counted -> 13 deducted, not 999.
	d1 = day("2027-01-05", "09:53:00")
	assert d1.custom_late_coming_minutes == 13, d1.custom_late_coming_minutes
	assert d1.custom_deduction_minutes == 13, d1.custom_deduction_minutes
	assert d1.custom_deducted_hours == flt(13 / 60.0, 3), d1.custom_deducted_hours

	# Late AND early on one day: both counted, added together.
	d2 = day("2027-01-06", "09:45:00", "18:00:00")  # 15 late -> 5, 30 early -> 25
	assert (d2.custom_late_coming_minutes, d2.custom_early_out_minutes) == (5, 25), (
		d2.custom_late_coming_minutes, d2.custom_early_out_minutes
	)
	assert d2.custom_deduction_minutes == 30, d2.custom_deduction_minutes

	# One minute late costs one minute, where a range would have charged a block.
	d3 = day("2027-01-07", "09:41:00")
	assert d3.custom_deduction_minutes == 1, d3.custom_deduction_minutes

	# Switching back to the ranges makes the same lateness cost the block again.
	policy = frappe.get_doc(POLICY, NAME)
	policy.deduction_basis = "Deduction Range"
	policy.save(ignore_permissions=True)
	d4 = day("2027-01-08", "09:41:00")
	assert d4.custom_deduction_minutes == 999, d4.custom_deduction_minutes


def run():
	check_measurement()
	check_allowance_and_walk()
	check_leave_walk()
	check_report_only()
	check_actual_minutes()
	frappe.db.rollback()

	_drop_policy()
	leave_type = frappe.db.get_value("Leave Type", {}, "name")

	# --- a well-formed policy saves -------------------------------------
	policy = _policy()
	assert policy.name == NAME, policy.name

	# --- Deduction Range lookup ----------------------------------------
	assert _slab_minutes(policy, "Late Coming", 1) == 30
	assert _slab_minutes(policy, "Late Coming", 30) == 30  # inclusive upper bound
	assert _slab_minutes(policy, "Late Coming", 31) == 60  # inclusive lower bound
	assert _slab_minutes(policy, "Early Out", 60) == 60
	assert _slab_minutes(policy, "Early Out", 500) == 120  # to_minutes 0 = catch-all
	# Below the lowest range: no row covers it, so nothing is deducted. This is
	# also how HR switches a band off — a gap in the table means no penalty.
	assert _slab_minutes(policy, "Late Coming", 0) == 0

	# a range that covers only late comings must not fire for an early out
	late_only = [{"applies_to": "Late Coming", "from_minutes": 1, "to_minutes": 0, "deduct_minutes": 45}]
	policy.deduction_slabs = []
	for row in late_only:
		policy.append("deduction_slabs", row)
	assert _slab_minutes(policy, "Late Coming", 20) == 45
	assert _slab_minutes(policy, "Early Out", 20) == 0

	frappe.delete_doc(POLICY, NAME, force=True, ignore_permissions=True)

	# --- validators reject what would make the walk non-deterministic ---
	assert _throws(lambda: _policy(slabs=[
		{"applies_to": "Both", "from_minutes": 1, "to_minutes": 30, "deduct_minutes": 30},
		{"applies_to": "Both", "from_minutes": 20, "to_minutes": 60, "deduct_minutes": 60},
	])), "overlapping ranges were accepted"

	assert _throws(lambda: _policy(slabs=[
		{"applies_to": "Both", "from_minutes": 30, "to_minutes": 10, "deduct_minutes": 30},
	])), "reversed range was accepted"

	assert _throws(lambda: _policy(slabs=[
		{"applies_to": "Both", "from_minutes": 1, "to_minutes": 0, "deduct_minutes": 30},
		{"applies_to": "Late Coming", "from_minutes": 61, "to_minutes": 0, "deduct_minutes": 60},
	])), "a range after the catch-all was accepted"

	assert _throws(lambda: _policy(priority=[
		{"source": "Working Hours"},
		{"source": "Leave", "leave_type": leave_type, "hours_per_unit": 2, "days_per_unit": 0.5},
	])), "a row after Working Hours was accepted"

	assert _throws(lambda: _policy(priority=[
		{"source": "Leave", "leave_type": leave_type, "hours_per_unit": 0, "days_per_unit": 0.5},
	])), "a zero conversion rate was accepted"

	frappe.db.rollback()
	print("deduction policy self-check OK (measurement, ranges, actual minutes, validators, grace quota, leave walk, report only)")
