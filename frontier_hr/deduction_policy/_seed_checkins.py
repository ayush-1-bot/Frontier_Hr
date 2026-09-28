"""Multi-punch Employee Checkin seeder — CHECKINS ONLY.

Run: bench --site <site> execute frontier_hr.deduction_policy._seed_checkins.run

Creates several IN/OUT pairs per employee per day and stops there. It does NOT
create Attendance and does NOT call process_auto_attendance, so the rows can be
marked from the Shift Type form by hand.

Re-runnable: clears only the checkins it made before (device_id = SEED_TAG) on
the dates it is about to write, and never touches Attendance.
"""

import frappe
from frappe.utils import add_days, get_datetime, getdate

SHIFT = "day"
EMPLOYEE_TAG = "_seed_deduction_demo"
SEED_TAG = "_seed_multi_checkin"

# Shift `day` is 09:30-18:30 with a 13:00-13:30 unpaid lunch window, and runs
# "Alternating entries as IN and OUT" + "Every Valid Check-in and Check-out",
# so every pair below is counted and the gaps between them are not.
#
# employee_name -> one list of (in, out) pairs per day.
PATTERNS = {
	"Oscar Ontime": [
		[("09:30:00", "11:30:00"), ("11:45:00", "13:00:00"), ("13:30:00", "16:30:00"), ("16:45:00", "18:30:00")],
		[("09:28:00", "13:00:00"), ("13:30:00", "18:32:00")],
		[("09:30:00", "12:00:00"), ("12:15:00", "15:00:00"), ("15:10:00", "18:30:00")],
		[("09:25:00", "13:05:00"), ("13:35:00", "18:35:00")],
	],
	"Latika Late": [
		[("10:15:00", "13:00:00"), ("13:30:00", "15:30:00"), ("15:45:00", "18:30:00")],
		[("09:50:00", "12:30:00"), ("12:45:00", "18:30:00")],
		[("10:25:00", "13:15:00"), ("13:45:00", "18:30:00")],
		[("11:00:00", "14:00:00"), ("14:15:00", "18:30:00")],
	],
	"Eshan Early": [
		[("09:30:00", "11:00:00"), ("11:15:00", "13:00:00"), ("13:30:00", "17:15:00")],
		[("09:30:00", "13:00:00"), ("13:30:00", "17:30:00")],
		[("09:30:00", "12:00:00"), ("12:20:00", "16:00:00")],
		[("09:30:00", "13:10:00"), ("13:25:00", "18:23:00")],
	],
	"Hema Halfday": [
		[("09:30:00", "11:00:00"), ("11:15:00", "13:00:00")],
		[("13:30:00", "16:00:00"), ("16:15:00", "18:30:00")],
		[("09:30:00", "11:30:00"), ("11:45:00", "13:15:00")],
		[("13:15:00", "15:45:00"), ("16:00:00", "18:30:00")],
	],
	"Eddie Edge": [
		# Odd log count: the trailing punch has no partner and earns nothing.
		[("09:30:00", "13:00:00"), ("13:30:00", "18:30:00"), ("19:00:00", None)],
		[("09:50:00", "12:00:00"), ("12:30:00", "17:30:00")],
		[("08:30:00", "13:00:00"), ("13:30:00", "19:25:00")],
		[("09:45:00", "11:00:00"), ("11:30:00", "13:00:00"), ("14:00:00", "18:30:00")],
	],
	"Pravin Punchy": [
		[("09:30:00", "13:00:00"), ("13:30:00", "18:30:00")],
		[("09:30:00", "13:15:00"), ("13:30:00", "18:30:00")],
		[("09:30:00", "11:00:00"), ("11:30:00", "18:30:00")],
		[("09:30:00", "13:00:00"), ("14:00:00", "18:30:00")],
	],
	"Manoj Multi": [
		[("09:30:00", "11:30:00"), ("12:00:00", "13:15:00"), ("13:45:00", "18:30:00")],
		[("09:30:00", "11:00:00"), ("11:15:00", "13:00:00"), ("13:30:00", "16:00:00"), ("16:15:00", "18:30:00")],
		[("09:30:00", "10:45:00"), ("11:00:00", "12:30:00"), ("12:45:00", "13:20:00"), ("13:40:00", "18:30:00")],
		# Every log stamped IN. Alternating mode ignores log_type and pairs by
		# position, so this still reads as one 09:30-18:30 span.
		[("09:30:00", "18:30:00")],
	],
}

# Days whose patterns are written with every log forced to IN.
FORCE_IN = {("Manoj Multi", 3)}


def _working_days(start, count):
	"""`count` weekdays from `start` that are not on the shift's holiday list."""
	holiday_list = frappe.db.get_value("Shift Type", SHIFT, "holiday_list")
	holidays = set()
	if holiday_list:
		holidays = set(frappe.get_all("Holiday", filters={"parent": holiday_list}, pluck="holiday_date"))

	days, date = [], getdate(start)
	while len(days) < count:
		if date.weekday() < 5 and date not in holidays:
			days.append(date)
		date = add_days(date, 1)
	return days


def run(start_date=None, days_count=4):
	employees = {
		e.employee_name: e.name
		for e in frappe.get_all(
			"Employee", filters={"employee_number": EMPLOYEE_TAG}, fields=["name", "employee_name"]
		)
	}
	if not employees:
		print(f"no employees tagged {EMPLOYEE_TAG} — run _seed_demo.run first")
		return

	# Default: the four working days ending yesterday, so nothing lands in the
	# future where auto-attendance would refuse to process it.
	days = _working_days(start_date or add_days(getdate(), -5), int(days_count))

	# Clear only this seeder's own checkins on these dates.
	stale = frappe.get_all(
		"Employee Checkin",
		filters={
			"device_id": SEED_TAG,
			"time": ["between", [f"{days[0]} 00:00:00", f"{days[-1]} 23:59:59"]],
		},
		pluck="name",
	)
	for name in stale:
		frappe.delete_doc("Employee Checkin", name, force=True, ignore_permissions=True)

	made = 0
	for employee_name, day_patterns in PATTERNS.items():
		employee = employees.get(employee_name)
		if not employee:
			continue

		for day_index, date in enumerate(days):
			pairs = day_patterns[day_index % len(day_patterns)]
			force_in = (employee_name, day_index) in FORCE_IN

			punches = [(t, kind) for a, b in pairs for kind, t in (("IN", a), ("OUT", b))]
			for time, log_type in punches:
				if not time:
					continue
				frappe.get_doc(
					{
						"doctype": "Employee Checkin",
						"employee": employee,
						"log_type": "IN" if force_in else log_type,
						"time": get_datetime(f"{date} {time}"),
						"shift": SHIFT,
						"device_id": SEED_TAG,
					}
				).insert(ignore_permissions=True)
				made += 1

	frappe.db.commit()

	print(f"cleared: {len(stale)} old seeded checkins")
	print(f"dates: {[str(d) for d in days]}")
	print(f"employees: {len(PATTERNS)}")
	print(f"checkins created: {made}")
	print("Attendance untouched — mark it from the Shift Type form.")
