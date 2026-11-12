"""Self-check for the nearest-shift pick. All data it creates is rolled back.
Run: bench --site <site> execute frontier_hr.multi_shift_checkin.test_multi_shift_checkin.run
"""

from unittest.mock import patch

import frappe

from frontier_hr.multi_shift_checkin.employee_checkin_class import CustomEmployeeCheckin

DAY = "2031-03-03"
NEXT_DAY = "2031-03-04"
MORNING, AFTERNOON, ADJACENT, NIGHT, EARLY = (f"_MSC {n}" for n in ("Morning", "Afternoon", "Adjacent", "Night", "Early"))


def _shift_type(name, start, end):
	doc = frappe.new_doc("Shift Type")
	doc.update(
		{
			"__newname": name,
			"start_time": start,
			"end_time": end,
			"begin_check_in_before_shift_start_time": 60,
			"allow_check_out_after_shift_end_time": 60,
			"determine_check_in_and_check_out": "Alternating entries as IN and OUT during the same shift",
			"working_hours_calculation_based_on": "First Check-in and Last Check-out",
		}
	)
	doc.insert()


def _employee():
	company = frappe.get_all("Company", pluck="name", limit=1)[0]
	doc = frappe.new_doc("Employee")
	doc.update(
		{
			"first_name": "_MSC Check",
			"gender": "Other",
			"date_of_birth": "1990-01-01",
			"date_of_joining": "2020-01-01",
			"company": company,
			"status": "Active",
		}
	)
	doc.flags.ignore_mandatory = True
	doc.insert()
	return doc


def _assign(employee, shift_type):
	doc = frappe.new_doc("Shift Assignment")
	doc.update(
		{"employee": employee.name, "company": employee.company, "shift_type": shift_type, "start_date": DAY}
	)
	doc.insert()
	doc.submit()


def _punch(employee, time, log_type=None):
	doc = frappe.new_doc("Employee Checkin")
	doc.update({"employee": employee.name, "time": time, "log_type": log_type})
	doc.insert()
	return doc


def _check(label, doc, shift, log_type):
	assert (doc.shift, doc.log_type or None) == (shift, log_type), f"{label}: got {doc.shift}, {doc.log_type}"
	print(f"ok  {label}: {doc.shift}, {doc.log_type}")


def _scenarios():
	_shift_type(MORNING, "09:00:00", "13:00:00")
	_shift_type(AFTERNOON, "14:00:00", "18:00:00")

	gap = _employee()
	_assign(gap, MORNING)
	_assign(gap, AFTERNOON)
	_check("blank first punch is IN", _punch(gap, f"{DAY} 08:55:00"), MORNING, "IN")
	_check("blank 13:10 closes morning", _punch(gap, f"{DAY} 13:10:00"), MORNING, "OUT")
	_check("blank 13:50 opens afternoon", _punch(gap, f"{DAY} 13:50:00"), AFTERNOON, "IN")
	_check("blank last punch is OUT", _punch(gap, f"{DAY} 18:05:00"), AFTERNOON, "OUT")

	device = _employee()
	_assign(device, MORNING)
	_assign(device, AFTERNOON)
	_check("device OUT 13:40 uses closest end", _punch(device, f"{DAY} 13:40:00", "OUT"), MORNING, "OUT")
	_check("device IN 13:20 uses closest start", _punch(device, f"{DAY} 13:20:00", "IN"), AFTERNOON, "IN")

	_shift_type(ADJACENT, "13:00:00", "18:00:00")
	no_gap = _employee()
	_assign(no_gap, MORNING)
	_assign(no_gap, ADJACENT)
	_check("no gap: device IN 12:50 is next shift", _punch(no_gap, f"{DAY} 12:50:00", "IN"), ADJACENT, "IN")

	_shift_type(NIGHT, "22:00:00", "06:00:00")
	_shift_type(EARLY, "07:00:00", "15:00:00")
	night = _employee()
	_assign(night, NIGHT)
	_assign(night, EARLY)
	_check("night shift IN", _punch(night, f"{DAY} 21:50:00"), NIGHT, "IN")
	_check("night OUT after midnight", _punch(night, f"{NEXT_DAY} 06:10:00"), NIGHT, "OUT")
	_check("next shift IN after midnight", _punch(night, f"{NEXT_DAY} 06:50:00"), EARLY, "IN")

	assert isinstance(frappe.new_doc("Employee Checkin"), CustomEmployeeCheckin), "override not active"
	print("ok  override class active")


def _standard_behaviour():
	"""Setting off: same 13:10 punch stays on hrms logic, Log Type stays blank."""
	employee = _employee()
	_assign(employee, MORNING)
	_assign(employee, AFTERNOON)
	_check("setting off keeps hrms result", _punch(employee, f"{DAY} 13:10:00"), AFTERNOON, None)


def run():
	real = frappe.db.get_single_value
	enabled = {"allow_multiple_shift_assignments": 1, "custom_pick_nearest_shift": 1}

	def setting(doctype, field, *args, **kwargs):
		return enabled[field] if doctype == "HR Settings" and field in enabled else real(doctype, field, *args, **kwargs)

	try:
		with (
			patch.object(frappe.db, "get_single_value", setting),
			patch("hrms.telemetry.capture"),
			patch("hrms.telemetry.capture_first"),
		):
			_scenarios()
			enabled["custom_pick_nearest_shift"] = 0
			_standard_behaviour()
		print("all passed")
	finally:
		frappe.db.rollback()
		for name in (MORNING, AFTERNOON, ADJACENT, NIGHT, EARLY):
			frappe.clear_document_cache("Shift Type", name)
