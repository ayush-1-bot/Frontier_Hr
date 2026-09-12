"""Recompute Attendance payroll hours when a Leave Application changes them.

hrms marks a leave day by REWRITING an existing Attendance row with db_set:

    # leave_application.py :: create_or_update_attendance
    doc.db_set({"status": status, "leave_type": ..., "leave_application": ...})

db_set goes straight to the database and runs no hooks, so
attendance.apply_buffer_logic never sees the change. The row then shows
'On Leave' in every list and report while custom_base_hours still holds what
the day was worth BEFORE the leave — 0 for a day auto-attendance had marked
Absent. The employee is shown as on approved paid leave and paid nothing for
it, with nothing in the data to explain the discrepancy.

Only the UPDATE path is affected. Where hrms creates a fresh Attendance for the
leave it goes through the ORM, hooks run, and the day is credited correctly.

Cancelling a Leave Application has the mirror problem: hrms puts the day back
to Absent with db_set, and without this the day would keep paying a full shift.
"""

import frappe
from frappe.utils import flt

from frontier_hr.overtime_extension.overrides.attendance import apply_buffer_logic

# Written back by hand rather than through doc.save(): these rows are submitted,
# and their payroll fields are not allow_on_submit. This is the same route the
# recompute patches take.
_RECOMPUTED_FIELDS = (
	"working_hours",
	"standard_working_hours",
	"custom_base_hours",
	"custom_total_ot_hours",
	"custom_early_ot_minutes",
	"custom_late_ot_minutes",
	"custom_ot_status",
	"overtime_type",
)


def recompute_attendance_hours(doc, method=None):
	"""Re-run the base/OT passes over every Attendance day this leave covers."""
	if not doc.get("employee") or not doc.get("from_date") or not doc.get("to_date"):
		return

	names = frappe.get_all(
		"Attendance",
		filters={
			"employee": doc.employee,
			"docstatus": ["<", 2],
			"attendance_date": ["between", [doc.from_date, doc.to_date]],
		},
		pluck="name",
	)

	for name in names:
		attendance = frappe.get_doc("Attendance", name)
		apply_buffer_logic(attendance)

		values = {}
		for fieldname in _RECOMPUTED_FIELDS:
			value = attendance.get(fieldname)
			values[fieldname] = value if fieldname in ("custom_ot_status", "overtime_type") else flt(value)

		frappe.db.set_value("Attendance", name, values, update_modified=False)
