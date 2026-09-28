"""Demo/test data seeder for the deduction policy work — NOT shipped behaviour.

Run: bench --site <site> execute frontier_hr.deduction_policy._seed_demo.run

Creates 5 employees on the `day` shift and a spread of Employee Checkin rows
covering every case the deduction and lunch-window passes have to get right,
then lets hrms build the Attendance rows from them via process_auto_attendance.

Re-runnable: it clears its own employees, their checkins and their attendance
first. It only ever touches records carrying SEED_TAG.
"""

import frappe
from frappe.utils import add_days, get_datetime, getdate

SHIFT = "day"
SEED_TAG = "_seed_deduction_demo"

# Matches the `day` shift: 09:30-18:30 with a 13:00-13:30 unpaid lunch.
SHIFT_START = "09:30:00"
SHIFT_END = "18:30:00"

EMPLOYEES = [
	("Oscar", "Ontime"),
	("Latika", "Late"),
	("Eshan", "Early"),
	("Hema", "Halfday"),
	("Eddie", "Edge"),
	("Pravin", "Punchy"),
	("Manoj", "Multi"),
]

# (employee index, day offset, in_time, out_time, what it exercises)
# out_time None = missing punch.
SCENARIOS = [
	# --- Oscar: clean days ------------------------------------------------
	(0, 0, "09:25:00", "18:35:00", "on time, covers lunch -> full 30 min off"),
	(0, 1, "09:30:00", "18:30:00", "exactly on the shift bounds"),
	(0, 2, "09:28:00", "18:32:00", "inside both grace periods"),
	# --- Latika: late coming tiers ---------------------------------------
	(1, 0, "09:35:00", "18:30:00", "5 min late -> inside 10 min grace -> no deduction"),
	(1, 1, "09:50:00", "18:30:00", "20 min late -> 1-30 range"),
	(1, 2, "10:25:00", "18:30:00", "55 min late -> 31-60 range"),
	(1, 3, "11:00:00", "18:30:00", "90 min late -> 61+ catch-all range"),
	# --- Eshan: early out tiers ------------------------------------------
	(2, 0, "09:30:00", "18:23:00", "7 min early -> past 5 min grace -> 1-30 range"),
	(2, 1, "09:30:00", "17:30:00", "60 min early -> 31-60 range"),
	(2, 2, "09:30:00", "16:00:00", "150 min early -> catch-all range"),
	# --- Hema: the lunch window ------------------------------------------
	(3, 0, "09:30:00", "13:00:00", "leaves AT lunch start -> no lunch off -> 3.5h"),
	(3, 1, "13:30:00", "18:30:00", "arrives AT lunch end -> no lunch off -> 5.0h"),
	(3, 2, "09:30:00", "13:15:00", "partial lunch overlap -> 15 min off -> 3.5h"),
	(3, 3, "13:15:00", "18:30:00", "partial lunch overlap -> 15 min off -> 5.0h"),
	# --- Eddie: edges -----------------------------------------------------
	(4, 0, "09:50:00", "17:30:00", "late AND early on the same day"),
	(4, 1, "09:45:00", None, "missing punch out"),
	# 19:25, not later: the shift only accepts a punch out up to
	# allow_check_out_after_shift_end_time (60) minutes past 18:30, and hrms
	# silently drops one outside that window, leaving the day Absent.
	(4, 2, "08:30:00", "19:25:00", "early in + late out -> overtime both ends"),
	# day 3 deliberately has no checkin at all -> absent
	# --- Pravin: MULTI-PUNCH days, the lunch punch-out ---------------------
	# Given as explicit pairs, so _punch_pairs is exercised against real
	# Employee Checkin rows rather than a single in/out span.
	(5, 0, [("09:30:00", "13:00:00"), ("13:30:00", "18:30:00")], None,
	 "punched out exactly over lunch -> gap already excluded -> 8.5"),
	(5, 1, [("09:30:00", "13:15:00"), ("13:30:00", "18:30:00")], None,
	 "punched out for half the window -> 15 min off -> 8.5"),
	(5, 2, [("09:30:00", "11:00:00"), ("11:30:00", "18:30:00")], None,
	 "break OUTSIDE lunch -> own time, lunch still charged -> 8.0"),
	(5, 3, [("09:30:00", "13:00:00"), ("14:00:00", "18:30:00")], None,
	 "hour-long lunch break -> only the 30 min window is free -> 8.0"),
	# --- Manoj: many entries a day, and the alternating-mode device quirk ---
	(6, 0, [("09:30:00", "11:30:00"), ("12:00:00", "13:15:00"), ("13:45:00", "18:30:00")], None,
	 "3 pairs, 6 logs -> raw 8.0, 15 min inside lunch -> 7.75"),
	(6, 1, [("09:30:00", "11:00:00"), ("11:15:00", "13:00:00"), ("13:30:00", "16:00:00"),
	        ("16:15:00", "18:30:00")], None,
	 "4 pairs, 8 logs, out across the whole window -> raw 8.0, nothing off -> 8.0"),
	(6, 2, [("09:30:00", "13:00:00"), ("13:30:00", "18:30:00"), ("19:00:00", None)], None,
	 "5 logs: odd trailing punch has no partner and earns nothing -> 8.5"),
	# Every log stamped IN. Alternating mode IGNORES log_type and pairs by
	# position, so this must still read as one 09:30-18:30 span and lose the
	# lunch break: 8.5, not 9.0. Pairing on log_type would find no pairs here.
	(6, 3, [("09:30:00", "18:30:00")], None, "device stamps every log IN -> 8.5", "IN"),
]


def _clear():
	employees = frappe.get_all("Employee", filters={"employee_number": SEED_TAG}, pluck="name")
	if not employees:
		return

	for doctype in ("Attendance", "Employee Checkin"):
		for name in frappe.get_all(doctype, filters={"employee": ["in", employees]}, pluck="name"):
			doc = frappe.get_doc(doctype, name)
			if doc.docstatus == 1:
				doc.flags.ignore_permissions = True
				doc.cancel()
			doc.delete(ignore_permissions=True, force=True)

	for name in frappe.get_all(
		"Shift Assignment", filters={"employee": ["in", employees]}, pluck="name"
	):
		doc = frappe.get_doc("Shift Assignment", name)
		if doc.docstatus == 1:
			doc.flags.ignore_permissions = True
			doc.cancel()
		doc.delete(ignore_permissions=True, force=True)

	for name in employees:
		frappe.delete_doc("Employee", name, force=True, ignore_permissions=True)


def _fix_shift():
	"""The two settings that make the requested scenarios reachable at all."""
	shift = frappe.get_doc("Shift Type", SHIFT)
	changes = {}

	# Stored as 1 AM, which never overlaps a 09:30-18:30 day.
	if str(shift.custom_lunch_start) != "13:00:00":
		changes["custom_lunch_start"] = "13:00:00"
		changes["custom_lunch_end"] = "13:30:00"

	# hrms tests the absent threshold FIRST, so an 8.0 absent bar with a 3.0
	# half-day bar marks every sub-8h day Absent and Half Day never fires.
	if shift.working_hours_threshold_for_absent >= shift.working_hours_threshold_for_half_day:
		changes["working_hours_threshold_for_absent"] = 2.0
		changes["working_hours_threshold_for_half_day"] = 5.0

	for field, value in changes.items():
		shift.set(field, value)
	if changes:
		shift.flags.ignore_permissions = True
		shift.save()
	return changes


def _working_days(start, count):
	"""`count` weekdays from `start` that are not on the shift's holiday list."""
	holiday_list = frappe.db.get_value("Shift Type", SHIFT, "holiday_list")
	holidays = set()
	if holiday_list:
		holidays = set(
			frappe.get_all("Holiday", filters={"parent": holiday_list}, pluck="holiday_date")
		)

	days, date = [], getdate(start)
	while len(days) < count:
		if date.weekday() < 5 and date not in holidays:
			days.append(date)
		date = add_days(date, 1)
	return days


def run():
	_clear()
	shift_changes = _fix_shift()

	company = frappe.db.get_value("Employee", {"default_shift": SHIFT}, "company")
	holiday_list = frappe.db.get_value("Shift Type", SHIFT, "holiday_list")
	days = _working_days(add_days(getdate(), -10), 4)

	# --- employees --------------------------------------------------------
	employees = []
	for first_name, last_name in EMPLOYEES:
		doc = frappe.get_doc(
			{
				"doctype": "Employee",
				"first_name": first_name,
				"last_name": last_name,
				"employee_number": SEED_TAG,
				"gender": frappe.db.get_value("Gender", {}, "name"),
				"date_of_birth": "1995-01-01",
				"date_of_joining": add_days(days[0], -365),
				"company": company,
				"status": "Active",
				"default_shift": SHIFT,
				"holiday_list": holiday_list,
			}
		).insert(ignore_permissions=True)
		employees.append(doc.name)

		frappe.get_doc(
			{
				"doctype": "Shift Assignment",
				"employee": doc.name,
				"shift_type": SHIFT,
				"company": company,
				"start_date": add_days(days[0], -30),
				"status": "Active",
			}
		).insert(ignore_permissions=True).submit()

	# --- checkins ---------------------------------------------------------
	made = 0
	for scenario in SCENARIOS:
		emp_idx, day_offset, in_time, out_time = scenario[:4]
		force_log_type = scenario[5] if len(scenario) > 5 else None
		employee = employees[emp_idx]
		date = days[day_offset]
		# A scenario is either a single in/out pair or an explicit list of pairs.
		pairs = in_time if isinstance(in_time, list) else [(in_time, out_time)]
		punches = [(t, kind) for a, b in pairs for kind, t in (("IN", a), ("OUT", b))]
		for time, log_type in punches:
			if not time:
				continue
			log_type = force_log_type or log_type
			frappe.get_doc(
				{
					"doctype": "Employee Checkin",
					"employee": employee,
					"log_type": log_type,
					"time": get_datetime(f"{date} {time}"),
					"shift": SHIFT,
					"device_id": SEED_TAG,
				}
			).insert(ignore_permissions=True)
			made += 1

	# --- let hrms build the Attendance rows -------------------------------
	shift = frappe.get_doc("Shift Type", SHIFT)
	shift.db_set("process_attendance_after", add_days(days[0], -1), update_modified=False)
	shift.db_set("last_sync_of_checkin", get_datetime(f"{days[-1]} 23:59:59"), update_modified=False)
	shift.reload()
	shift.process_auto_attendance()

	frappe.db.commit()

	print(f"shift changes: {shift_changes or 'none needed'}")
	print(f"employees: {employees}")
	print(f"dates: {[str(d) for d in days]}")
	print(f"checkins created: {made}")
	print(f"attendance rows: {frappe.db.count('Attendance', {'employee': ['in', employees]})}")
