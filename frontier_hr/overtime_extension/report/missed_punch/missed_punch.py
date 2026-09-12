"""Missed Punch — days whose punches cannot produce a working day.

The shifts here measure hours as FIRST punch in to LAST punch out
(Shift Type.working_hours_calculation_based_on), so the IN/OUT log type on a
checkin is not used to price anything and is not tested here either. What
matters is whether the day has two ends to measure between.

Three faults, in the order they hurt:

  Only One Punch          a single checkin for the whole day. There is no
                          second end, so hrms records 0 working hours and the
                          day usually falls under the Absent threshold — the
                          employee worked and is marked absent.
  Missing In/Out          the Attendance row carries exactly one of
                          in_time / out_time. Same fault seen from the
                          attendance side, and it also catches hand-made
                          attendance with no checkins behind it.
  No Attendance Marked    punches exist but no Attendance row was created,
                          usually a delayed biometric sync.

This is the same definition as the nightly mailer in
overtime_extension/tasks.py::get_miss_punch_records, widened only by the
checkin side: the mailer tests Attendance.in_time / out_time, which hrms fills
from the first and last punch, so a day with a single punch can still leave
both populated and slip past it.

Days are grouped by SHIFT OCCURRENCE (Employee Checkin.shift_start), never by
calendar date. A 22:00-06:00 night shift puts its IN on one date and its OUT on
the next, so grouping by date splits one sound shift into two single-punch days
and reports both — measured on recode.com as 10 of 14 August rows being one
night-shift employee whose attendance was in fact Present with 7.83 h. hrms
dates the Attendance row to the shift start too, so the two line up.

Excluded, matching the mailer: employees on approved leave that day (a leave
day needs no punches) and inactive employees (a leaver cannot be chased for a
correction).
"""

import frappe
from frappe.utils import flt, getdate

ISSUE_SINGLE_PUNCH = "Only One Punch"
ISSUE_MISSING_IN_OUT = "Missing In/Out"
ISSUE_NO_ATTENDANCE = "No Attendance Marked"


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.from_date or not filters.to_date:
		frappe.throw("Set both From Date and To Date.")
	if getdate(filters.to_date) < getdate(filters.from_date):
		frappe.throw("To Date cannot be before From Date.")

	rows = _get_rows(filters)
	if filters.issue:
		rows = [r for r in rows if r["issue"] == filters.issue]

	rows.sort(key=lambda r: (str(r["punch_date"]), r["employee"]), reverse=True)
	return _columns(), rows


def _get_rows(filters):
	days = {}

	for day in _punch_days(filters):
		days[(day.employee, day.punch_date)] = day

	# Attendance-side pass. A half-empty in/out with no checkins behind it —
	# somebody marking attendance by hand — never appears in the checkin query.
	for att in _half_punched_attendance(filters):
		key = (att.employee, att.punch_date)
		if key not in days:
			days[key] = frappe._dict(
				{
					"employee": att.employee,
					"employee_name": att.employee_name,
					"company": att.company,
					"punch_date": att.punch_date,
					"shift": att.shift,
					"punch_count": 0,
					"first_punch": None,
					"last_punch": None,
					"punch_log": None,
				}
			)

	rows = []
	for (employee, punch_date), day in days.items():
		attendance = frappe.db.get_value(
			"Attendance",
			{"employee": employee, "attendance_date": punch_date, "docstatus": 1},
			["name", "status", "working_hours", "custom_base_hours", "in_time", "out_time"],
			as_dict=True,
		)

		issue = _classify(day, attendance)
		if not issue:
			continue

		rows.append(
			{
				"employee": employee,
				"employee_name": day.employee_name,
				"company": day.company,
				"punch_date": punch_date,
				"shift": day.shift,
				"issue": issue,
				"punch_count": day.punch_count,
				"first_punch": day.first_punch,
				"last_punch": day.last_punch,
				"punch_log": day.punch_log,
				"attendance": attendance.name if attendance else None,
				"status": attendance.status if attendance else "Not Marked",
				"in_time": attendance.in_time if attendance else None,
				"out_time": attendance.out_time if attendance else None,
				"working_hours": flt(attendance.working_hours) if attendance else 0.0,
				"base_hours": flt(attendance.custom_base_hours) if attendance else 0.0,
			}
		)

	return rows


def _classify(day, attendance):
	"""The fault worth reporting, or None when the day reads cleanly."""
	if flt(day.punch_count) == 1:
		return ISSUE_SINGLE_PUNCH

	if attendance and bool(attendance.in_time) != bool(attendance.out_time):
		return ISSUE_MISSING_IN_OUT

	if not attendance and flt(day.punch_count):
		return ISSUE_NO_ATTENDANCE

	return None


def _filter_conditions(filters, alias, date_expression):
	"""Shared WHERE parts. `alias` is the checkin/attendance alias, and
	`date_expression` how the day is derived from it."""
	conditions = [f"{date_expression} BETWEEN %(from_date)s AND %(to_date)s"]
	values = {"from_date": filters.from_date, "to_date": filters.to_date}

	if filters.employee:
		conditions.append(f"{alias}.employee = %(employee)s")
		values["employee"] = filters.employee
	if filters.company:
		conditions.append("e.company = %(company)s")
		values["company"] = filters.company

	# Same two exclusions the nightly mailer applies.
	conditions.append("e.status = 'Active'")
	conditions.append(
		f"""NOT EXISTS (
			SELECT 1 FROM `tabLeave Application` la
			WHERE la.employee = {alias}.employee
			  AND la.status = 'Approved'
			  AND la.docstatus = 1
			  AND {date_expression} BETWEEN la.from_date AND la.to_date
		)"""
	)
	return conditions, values


def _punch_days(filters):
	# COALESCE: shift_start is set by hrms when it assigns a checkin to a shift,
	# and is null only for punches outside any shift window — those fall back to
	# their own date.
	shift_day = "DATE(COALESCE(c.shift_start, c.time))"
	conditions, values = _filter_conditions(filters, "c", shift_day)
	if filters.shift:
		conditions.append("c.shift = %(shift)s")
		values["shift"] = filters.shift

	return frappe.db.sql(
		f"""
		SELECT
			c.employee,
			e.employee_name,
			e.company,
			DATE(COALESCE(c.shift_start, c.time)) AS punch_date,
			MAX(c.shift)        AS shift,
			COUNT(*)            AS punch_count,
			MIN(c.time)         AS first_punch,
			MAX(c.time)         AS last_punch,
			GROUP_CONCAT(TIME_FORMAT(c.time, '%%H:%%i') ORDER BY c.time SEPARATOR ',  ') AS punch_log
		FROM `tabEmployee Checkin` c
		INNER JOIN `tabEmployee` e ON e.name = c.employee
		WHERE {" AND ".join(conditions)}
		GROUP BY c.employee, DATE(COALESCE(c.shift_start, c.time)), e.employee_name, e.company
		""",
		values,
		as_dict=True,
	)


def _half_punched_attendance(filters):
	conditions, values = _filter_conditions(filters, "a", "a.attendance_date")
	conditions.append("a.docstatus = 1")
	conditions.append("(a.in_time IS NULL) != (a.out_time IS NULL)")
	if filters.shift:
		conditions.append("a.shift = %(shift)s")
		values["shift"] = filters.shift

	return frappe.db.sql(
		f"""
		SELECT
			a.employee,
			a.employee_name,
			a.company,
			a.attendance_date AS punch_date,
			a.shift
		FROM `tabAttendance` a
		INNER JOIN `tabEmployee` e ON e.name = a.employee
		WHERE {" AND ".join(conditions)}
		""",
		values,
		as_dict=True,
	)


def _columns():
	return [
		{"label": "Employee", "fieldname": "employee", "fieldtype": "Link", "options": "Employee", "width": 110},
		{"label": "Employee Name", "fieldname": "employee_name", "fieldtype": "Data", "width": 150},
		{"label": "Date", "fieldname": "punch_date", "fieldtype": "Date", "width": 95},
		{"label": "Issue", "fieldname": "issue", "fieldtype": "Data", "width": 165},
		{"label": "Punches", "fieldname": "punch_log", "fieldtype": "Data", "width": 200},
		{"label": "Count", "fieldname": "punch_count", "fieldtype": "Int", "width": 70},
		{"label": "In Time", "fieldname": "in_time", "fieldtype": "Datetime", "width": 145},
		{"label": "Out Time", "fieldname": "out_time", "fieldtype": "Datetime", "width": 145},
		{"label": "Shift", "fieldname": "shift", "fieldtype": "Link", "options": "Shift Type", "width": 150},
		{"label": "Attendance", "fieldname": "attendance", "fieldtype": "Link", "options": "Attendance", "width": 150},
		{"label": "Status", "fieldname": "status", "fieldtype": "Data", "width": 100},
		{"label": "Working Hours", "fieldname": "working_hours", "fieldtype": "Float", "precision": 2, "width": 115},
		{"label": "Base Hours", "fieldname": "base_hours", "fieldtype": "Float", "precision": 2, "width": 105},
		{"label": "Company", "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 140},
	]
