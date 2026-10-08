# Copyright (c) 2026, Frontier Softech
# For license information, please see license.txt

"""What the deduction policy did, or would have done.

Rows come out the same whether the policy is live or on Report Only — the save
pass records the figures either way. The Mode column says which, so a half day
of leave shown against a Report Only row is never read as one actually taken.
"""

import frappe
from frappe import _

STATUS_COLOURS = {"Deducted": "red", "Report Only": "orange", "Within Grace": "green"}


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("from_date") or not filters.get("to_date"):
		frappe.throw(_("From Date and To Date are required."))

	rows = _fetch(filters)
	_attach_breakup(rows)
	return _columns(), rows, None, _chart(rows), _summary(rows)


def _fetch(filters):
	conditions = ["a.docstatus < 2", "a.attendance_date BETWEEN %(from_date)s AND %(to_date)s"]
	# A row the policy never judged has no policy stamped; those are noise here.
	conditions.append("a.custom_deduction_policy IS NOT NULL")
	# So is a day nobody was late on — this is a report about lateness.
	conditions.append(
		"(a.custom_late_coming_raw_minutes > 0 OR a.custom_early_out_raw_minutes > 0"
		" OR a.custom_late_coming_minutes > 0 OR a.custom_early_out_minutes > 0)"
	)

	for field, clause in (
		("company", "a.company = %(company)s"),
		("employee", "a.employee = %(employee)s"),
		("department", "a.department = %(department)s"),
		("shift", "a.shift = %(shift)s"),
		("policy", "a.custom_deduction_policy = %(policy)s"),
	):
		if filters.get(field):
			conditions.append(clause)

	if filters.get("branch"):
		conditions.append("e.branch = %(branch)s")

	mode = filters.get("mode")
	if mode == "Deducted":
		conditions.append("a.custom_deduction_minutes > 0 AND a.custom_deduction_report_only = 0")
	elif mode == "Report Only":
		conditions.append("a.custom_deduction_report_only = 1")
	elif mode == "Within Grace":
		# Late or early, but the grace swallowed it: nothing was charged.
		conditions.append(
			"a.custom_deduction_minutes = 0"
			" AND (a.custom_late_coming_raw_minutes > 0 OR a.custom_early_out_raw_minutes > 0)"
		)

	return frappe.db.sql(
		"""
		SELECT
			a.name AS attendance, a.employee, a.employee_name, a.attendance_date,
			e.branch, a.department, a.shift, a.status,
			a.custom_late_coming_raw_minutes AS late_raw,
			a.custom_late_coming_minutes AS late,
			a.custom_early_out_raw_minutes AS early_raw,
			a.custom_early_out_minutes AS early,
			a.custom_deduction_minutes AS deduction_minutes,
			a.custom_deducted_leave_days AS leave_days,
			a.custom_deducted_hours AS hours,
			a.custom_deduction_report_only AS report_only,
			a.custom_deduction_policy AS policy
		FROM `tabAttendance` a
		LEFT JOIN `tabEmployee` e ON e.name = a.employee
		WHERE {conditions}
		ORDER BY a.employee, a.attendance_date
		""".format(conditions=" AND ".join(conditions)),
		filters,
		as_dict=True,
	)


def _attach_breakup(rows):
	"""One readable line per row: where the deduction was taken from."""
	if not rows:
		return

	entries = frappe.db.sql(
		"""
		SELECT parent, source, leave_type, minutes, leave_days, hours
		FROM `tabAttendance Deduction Entry`
		WHERE parent IN %(parents)s
		ORDER BY parent, idx
		""",
		{"parents": [r.attendance for r in rows]},
		as_dict=True,
	)

	by_parent = {}
	for entry in entries:
		label = entry.leave_type if entry.source == "Leave" else _("Working Hours")
		unit = f"{entry.leave_days} d" if entry.source == "Leave" else f"{entry.hours} h"
		by_parent.setdefault(entry.parent, []).append(f"{label} {unit} ({entry.minutes}m)")

	for row in rows:
		row.breakup = " + ".join(by_parent.get(row.attendance, [])) or ""
		if row.report_only and row.deduction_minutes:
			row.mode = "Report Only"
		elif row.deduction_minutes:
			row.mode = "Deducted"
		else:
			row.mode = "Within Grace"


def _summary(rows):
	deducted = [r for r in rows if r.mode == "Deducted"]
	projected = [r for r in rows if r.mode == "Report Only"]
	return [
		{
			"label": _("Deducted"),
			"value": sum(r.deduction_minutes for r in deducted),
			"indicator": "Red",
			"datatype": "Int",
		},
		{
			"label": _("Report Only"),
			"value": sum(r.deduction_minutes for r in projected),
			"indicator": "Orange",
			"datatype": "Int",
		},
		{
			"label": _("Leave Days"),
			"value": sum(r.leave_days or 0 for r in deducted),
			"indicator": "Blue",
			"datatype": "Float",
		},
		{
			"label": _("Within Grace"),
			"value": len([r for r in rows if r.mode == "Within Grace"]),
			"indicator": "Green",
			"datatype": "Int",
		},
	]


def _chart(rows):
	if not rows:
		return None

	totals = {}
	for row in rows:
		totals.setdefault(row.employee_name, 0)
		totals[row.employee_name] += row.deduction_minutes or 0

	top = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:10]
	return {
		"data": {
			"labels": [name for name, _total in top],
			"datasets": [{"name": _("Deduction (Minutes)"), "values": [total for _name, total in top]}],
		},
		"type": "bar",
	}


def _columns():
	return [
		{"label": _("Attendance"), "fieldname": "attendance", "fieldtype": "Link",
		 "options": "Attendance", "width": 130},
		{"label": _("Date"), "fieldname": "attendance_date", "fieldtype": "Date", "width": 95},
		{"label": _("Employee"), "fieldname": "employee", "fieldtype": "Link",
		 "options": "Employee", "width": 110},
		{"label": _("Name"), "fieldname": "employee_name", "fieldtype": "Data", "width": 140},
		{"label": _("Shift"), "fieldname": "shift", "fieldtype": "Link",
		 "options": "Shift Type", "width": 90},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 90},
		{"label": _("Late (Raw)"), "fieldname": "late_raw", "fieldtype": "Int", "width": 90},
		{"label": _("Late"), "fieldname": "late", "fieldtype": "Int", "width": 70},
		{"label": _("Early (Raw)"), "fieldname": "early_raw", "fieldtype": "Int", "width": 95},
		{"label": _("Early"), "fieldname": "early", "fieldtype": "Int", "width": 70},
		{"label": _("Deduction (Min)"), "fieldname": "deduction_minutes", "fieldtype": "Int", "width": 120},
		{"label": _("Leave Days"), "fieldname": "leave_days", "fieldtype": "Float",
		 "precision": 3, "width": 100},
		{"label": _("Hours"), "fieldname": "hours", "fieldtype": "Float", "precision": 3, "width": 80},
		{"label": _("Taken From"), "fieldname": "breakup", "fieldtype": "Data", "width": 260},
		{"label": _("Mode"), "fieldname": "mode", "fieldtype": "Data", "width": 110},
		{"label": _("Policy"), "fieldname": "policy", "fieldtype": "Link",
		 "options": "Late Coming and Early Out Deduction Policy", "width": 200},
	]
