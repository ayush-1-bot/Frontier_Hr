"""
Credit for days with no Attendance
----------------------------------
Holidays and weekly offs never produce an Attendance row, so the submit hook
never sees them and they earn nothing. This walks those days instead and
credits whatever the policy's table gives "Holiday" and "Weekly Off".

Idempotent by watermark: `custom_attendance_credit_upto` on the Employee is the
last day already walked, so a day is only ever credited once however often the
job runs.

A day that HAS an Attendance row is skipped here and left to the submit hook —
including a day still waiting on a late biometric sync. The watermark moving
past it costs nothing, because the hook credits that row independently when it
finally arrives.
"""

import frappe
from frappe.utils import add_days, flt, getdate, today

from frontier_hr.leave_accrual.overrides.attendance import (
	POLICY_DOCTYPE,
	_apply_credit,
	_resolve_policy,
)

# How far back a first run is willing to walk, so an employee with no watermark
# does not replay years of holidays on the day the feature is switched on.
MAX_CATCHUP_DAYS = 31


def credit_non_working_days(date_str=None):
	"""Credit holidays and weekly offs up to `date_str` (yesterday by default)."""
	upto = getdate(date_str) if date_str else add_days(getdate(today()), -1)

	employees = frappe.get_all(
		"Employee",
		filters={"status": "Active"},
		fields=["name", "company", "department", "holiday_list",
		        "custom_attendance_credit_upto"],
	)

	walked = credited = 0
	for employee in employees:
		start = (
			add_days(getdate(employee.custom_attendance_credit_upto), 1)
			if employee.custom_attendance_credit_upto
			else add_days(upto, -MAX_CATCHUP_DAYS)
		)
		if start > upto:
			continue

		date = start
		while date <= upto:
			try:
				if _credit_day(employee, date):
					credited += 1
			except Exception:
				frappe.log_error(frappe.get_traceback(), "Leave Accrual: non-working day credit")
			date = add_days(date, 1)
			walked += 1

		frappe.db.set_value(
			"Employee", employee.name, "custom_attendance_credit_upto", upto,
			update_modified=False,
		)

	frappe.db.commit()
	frappe.logger().info(f"Leave Accrual: walked {walked} days, credited {credited}")
	return {"walked": walked, "credited": credited, "upto": str(upto)}


def _credit_day(employee, date):
	"""Credit one day if it is a holiday or weekly off with no Attendance."""
	# An Attendance row owns the day; the submit hook credits it.
	if frappe.db.exists(
		"Attendance",
		{"employee": employee.name, "attendance_date": date, "docstatus": ["!=", 2]},
	):
		return False

	day_type = _day_type(employee, date)
	if not day_type:
		return False  # an ordinary working day with nothing marked: not our business

	doc = frappe._dict(
		doctype="Attendance",
		name=None,
		employee=employee.name,
		company=employee.company,
		department=employee.department,
		attendance_date=date,
		status=day_type,
	)
	policy = _resolve_policy(doc)
	if not policy:
		return False

	credit = _credit_for_day_type(policy, day_type)
	if not credit:
		return False

	_apply_credit(doc, policy, credit)
	return True


def _day_type(employee, date):
	"""'Weekly Off', 'Holiday', or None for an ordinary working day.

	Weekly offs are told apart from public holidays by the Holiday row's own
	`weekly_off` flag, so a policy can rate a Sunday differently from Diwali.
	"""
	holiday_list = employee.holiday_list or frappe.db.get_value(
		"Company", employee.company, "default_holiday_list"
	)
	if not holiday_list:
		# No list to consult: fall back to the weekend, the same assumption
		# vip_attendance makes.
		return "Weekly Off" if getdate(date).weekday() in (5, 6) else None

	row = frappe.db.get_value(
		"Holiday",
		{"parent": holiday_list, "holiday_date": date},
		["name", "weekly_off"],
		as_dict=True,
	)
	if not row:
		return None
	return "Weekly Off" if row.weekly_off else "Holiday"


def _credit_for_day_type(policy, day_type):
	for row in policy.credits:
		if row.attendance_status == day_type:
			return flt(row.credit)
	return 0.0
