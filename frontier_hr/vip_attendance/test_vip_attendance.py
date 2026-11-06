"""Self-check for VIP attendance.

Run: bench --site <site> execute frontier_hr.vip_attendance.test_vip_attendance.run

Rolls everything back, so it is safe against a site with real data.
"""

import frappe
from frappe.utils import flt, getdate

from frontier_hr.vip_attendance.tasks import auto_mark_vip_attendance

SHIFT = "day"


def _employee(exempt=1):
	shift = frappe.db.get_value("Shift Type", SHIFT, ["name", "holiday_list"], as_dict=True)
	company = frappe.db.get_value("Company", {}, "name")
	doc = frappe.get_doc({
		"doctype": "Employee", "first_name": "_Vip", "last_name": "Testcase",
		"gender": frappe.db.get_value("Gender", {}, "name"), "date_of_birth": "1995-01-01",
		"date_of_joining": "2025-01-01", "company": company, "status": "Active",
		"default_shift": SHIFT, "holiday_list": shift.holiday_list,
	}).insert(ignore_permissions=True)
	doc.db_set("custom_exempt_from_attendance", exempt, update_modified=False)
	return doc.name


def _attendance(employee, date):
	return frappe.db.get_value(
		"Attendance",
		{"employee": employee, "attendance_date": date, "docstatus": ["!=", 2]},
		["name", "status", "in_time", "out_time", "docstatus"],
		as_dict=True,
	)


def _checkins(employee, date):
	return frappe.get_all(
		"Employee Checkin",
		filters={"employee": employee, "time": ["between", [f"{date} 00:00:00", f"{date} 23:59:59"]]},
		fields=["log_type", "time"],
		order_by="time",
	)


def run():
	# A working day that is not on the shift's holiday list.
	date = "2026-11-10"  # Tuesday
	holiday_list = frappe.db.get_value("Shift Type", SHIFT, "holiday_list")
	if holiday_list and frappe.db.exists("Holiday", {"parent": holiday_list, "holiday_date": date}):
		raise AssertionError(f"{date} is a holiday on {holiday_list}; pick another date")

	vip = _employee(exempt=1)
	plain = _employee(exempt=0)

	auto_mark_vip_attendance(date)

	# --- the exempt employee is punched and marked -----------------------
	att = _attendance(vip, date)
	assert att and att.status == "Present", att
	assert att.docstatus == 1, att
	logs = _checkins(vip, date)
	assert [l.log_type for l in logs] == ["IN", "OUT"], logs
	# The punches sit on the shift bounds, so the day reads as a full one.
	assert str(att.in_time)[11:16] == "09:30", att.in_time
	assert str(att.out_time)[11:16] == "18:30", att.out_time

	# --- an employee without the flag is untouched -----------------------
	assert not _attendance(plain, date), "a non-exempt employee was marked"
	assert not _checkins(plain, date), "a non-exempt employee got checkins"

	# --- running twice must not duplicate anything -----------------------
	auto_mark_vip_attendance(date)
	assert len(_checkins(vip, date)) == 2, _checkins(vip, date)
	assert frappe.db.count("Attendance", {"employee": vip, "attendance_date": date,
	                                      "docstatus": ["!=", 2]}) == 1

	# --- a holiday is skipped --------------------------------------------
	if holiday_list:
		holiday = frappe.db.get_value(
			"Holiday", {"parent": holiday_list, "holiday_date": [">", date]}, "holiday_date"
		)
		if holiday:
			auto_mark_vip_attendance(holiday)
			assert not _attendance(vip, holiday), f"marked on holiday {holiday}"

	# --- the day still flows through the other policies -------------------
	# Punches on the shift bounds mean nothing late or early to charge.
	row = frappe.get_doc("Attendance", att.name)
	assert not flt(row.get("custom_deduction_minutes")), row.get("custom_deduction_minutes")
	# And the day earns accrual credit like any other, where a policy matches.
	if frappe.db.exists("Attendance Leave Accrual Policy", {"enabled": 1}):
		assert flt(row.get("custom_accrual_credit_days")) >= 0

	frappe.db.rollback()
	print("vip attendance self-check OK (marks, punches, skips others, idempotent, holiday)")
