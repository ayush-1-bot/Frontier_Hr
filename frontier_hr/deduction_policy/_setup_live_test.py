"""Persistent test bed for the deduction policy — leaves everything in place.

Run: bench --site <site> execute frontier_hr.deduction_policy._setup_live_test.run

Unlike test_deduction_policy, this COMMITS. It creates a real policy, a Short
Leave type, leave allocations sized so each employee takes a different path
through the priority table, then marks the seeded checkins so the deduction
runs for real.

Re-runnable: clears the Attendance it marked over the window and re-marks it.
"""

import frappe
from frappe.utils import add_days, get_datetime, getdate

SHIFT = "day"
EMPLOYEE_TAG = "_seed_deduction_demo"
POLICY = "Late Coming and Early Out Deduction Policy"
POLICY_NAME = "Frontier Late Coming and Early Out Policy"

SHORT_LEAVE = "Short Leave"
CASUAL_LEAVE = "Casual Leave"

# Balances chosen so each employee falls out of the priority table at a
# different point. Short Leave is 2 hours = 0.5 day, so 0.25 day buys 60 min.
#   employee_name -> (short leave days, casual leave days)
ALLOCATIONS = {
	"Oscar Ontime": (1.0, 2.0),    # plenty of both, should never reach hours
	"Latika Late": (0.25, 1.0),    # short leave runs out, spills to casual
	"Eshan Early": (0.0, 1.0),     # no short leave at all, straight to casual
	"Hema Halfday": (0.5, 0.0),    # short leave only, then straight to hours
	"Eddie Edge": (0.0, 0.0),      # no leave anywhere, everything hits hours
	"Pravin Punchy": (0.25, 0.25), # both thin, expect a three-way split
	"Manoj Multi": (2.0, 2.0),     # never short of leave
}


def _ensure_leave_type(name):
	if not frappe.db.exists("Leave Type", name):
		frappe.get_doc(
			{
				"doctype": "Leave Type",
				"leave_type_name": name,
				"max_leaves_allowed": 30,
				"include_holiday": 0,
			}
		).insert(ignore_permissions=True)
	return name


def _allocate(employee, leave_type, days, company):
	"""One submitted allocation per employee/type for the year. Skipped where
	the employee already has one, so re-running does not stack balances."""
	if not days:
		return
	existing = frappe.db.exists(
		"Leave Allocation",
		{
			"employee": employee,
			"leave_type": leave_type,
			"from_date": "2026-01-01",
			"docstatus": 1,
		},
	)
	if existing:
		return

	frappe.get_doc(
		{
			"doctype": "Leave Allocation",
			"employee": employee,
			"leave_type": leave_type,
			"from_date": "2026-01-01",
			"to_date": "2026-12-31",
			"new_leaves_allocated": days,
			"company": company,
			"carry_forward": 0,
		}
	).insert(ignore_permissions=True).submit()


def _build_policy(company):
	if frappe.db.exists(POLICY, POLICY_NAME):
		frappe.delete_doc(POLICY, POLICY_NAME, force=True, ignore_permissions=True)
	frappe.clear_document_cache(POLICY, POLICY_NAME)

	return frappe.get_doc(
		{
			"doctype": POLICY,
			"policy_name": POLICY_NAME,
			"enabled": 1,
			"company": company,
			"shift_type": SHIFT,
			"late_grace_minutes": 10,
			"early_grace_minutes": 5,
			"allowance_period": "Monthly",
			"allowed_late_count": 2,
			"allowed_early_count": 2,
			"count_together": 0,
			"deduction_slabs": [
				{"applies_to": "Both", "from_minutes": 1, "to_minutes": 30, "deduct_minutes": 30},
				{"applies_to": "Both", "from_minutes": 31, "to_minutes": 60, "deduct_minutes": 60},
				{"applies_to": "Both", "from_minutes": 61, "to_minutes": 0, "deduct_minutes": 120},
			],
			"deduction_priority": [
				{"source": "Leave", "leave_type": SHORT_LEAVE, "hours_per_unit": 2, "days_per_unit": 0.5},
				{"source": "Leave", "leave_type": CASUAL_LEAVE, "hours_per_unit": 8, "days_per_unit": 1},
				{"source": "Working Hours"},
			],
		}
	).insert(ignore_permissions=True)


def _clear_attendance(employees, start, end):
	for name in frappe.get_all(
		"Attendance",
		filters={"employee": ["in", employees], "attendance_date": ["between", [start, end]]},
		pluck="name",
	):
		doc = frappe.get_doc("Attendance", name)
		if doc.docstatus == 1:
			doc.flags.ignore_permissions = True
			doc.cancel()
		doc.delete(ignore_permissions=True, force=True)


def run():
	employees = {
		e.employee_name: e.name
		for e in frappe.get_all(
			"Employee", filters={"employee_number": EMPLOYEE_TAG}, fields=["name", "employee_name"]
		)
	}
	if not employees:
		print(f"no employees tagged {EMPLOYEE_TAG} — run _seed_demo.run first")
		return

	company = frappe.db.get_value("Employee", list(employees.values())[0], "company")

	# The window the seeded multi-punch checkins live in.
	logs = frappe.get_all(
		"Employee Checkin",
		filters={"device_id": "_seed_multi_checkin"},
		fields=["time"],
		order_by="time asc",
	)
	if not logs:
		print("no checkins tagged _seed_multi_checkin — run _seed_checkins.run first")
		return
	start, end = getdate(logs[0].time), getdate(logs[-1].time)

	# --- leave types and balances ----------------------------------------
	_ensure_leave_type(SHORT_LEAVE)
	_ensure_leave_type(CASUAL_LEAVE)
	for employee_name, (short_days, casual_days) in ALLOCATIONS.items():
		employee = employees.get(employee_name)
		if not employee:
			continue
		_allocate(employee, SHORT_LEAVE, short_days, company)
		_allocate(employee, CASUAL_LEAVE, casual_days, company)

	# --- the policy -------------------------------------------------------
	policy = _build_policy(company)

	# --- re-mark the window ----------------------------------------------
	_clear_attendance(list(employees.values()), start, end)

	shift = frappe.get_doc("Shift Type", SHIFT)
	# Start the day before the first seeded log, so the run does not replay
	# weeks of empty days as Absent.
	shift.db_set("process_attendance_after", add_days(start, -1), update_modified=False)
	shift.db_set("last_sync_of_checkin", get_datetime(f"{end} 23:59:59"), update_modified=False)
	shift.reload()
	shift.process_auto_attendance()

	frappe.db.commit()

	marked = frappe.db.count(
		"Attendance",
		{"employee": ["in", list(employees.values())], "attendance_date": ["between", [start, end]]},
	)
	print(f"policy      : {policy.name} (enabled, shift={SHIFT})")
	print(f"leave types : {SHORT_LEAVE}, {CASUAL_LEAVE}")
	print(f"window      : {start} .. {end}")
	print(f"attendance  : {marked} rows marked")
	print("committed — left in place for manual testing")
