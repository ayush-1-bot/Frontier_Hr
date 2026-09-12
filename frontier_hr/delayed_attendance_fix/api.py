"""
Fix Delayed Biometric Sync Attendance
--------------------------------------
Biometric device/server sometimes drops a checkin log and it arrives late
(e.g. next morning). By then Shift Type.process_auto_attendance has already
marked Attendance off the single log that did arrive (usually Absent / wrong
hours), and hrms core never reprocesses it: get_employee_checkins() only
looks at unlinked checkins, and create_or_update_attendance() throws
"already marked" once a second log shows up for a date that already has an
Attendance, so the late log sits orphaned forever.

This module finds those broken records and fixes them by cancelling the
wrong Attendance (which auto-unlinks its checkin via core's
Attendance.on_cancel -> unlink_attendance_from_checkins) and re-running
Shift Type.process_auto_attendance, which is the existing core method
already used by the "Mark Attendance" button. No hrms core file is modified.
"""

import frappe
from frappe import _
from frappe.utils import getdate

MAX_RANGE_DAYS = 180


def _validate_date_range(from_date, to_date):
	from_date = getdate(from_date)
	to_date = getdate(to_date)

	if to_date < from_date:
		frappe.throw(_("To Date cannot be before From Date."))

	if (to_date - from_date).days > MAX_RANGE_DAYS:
		frappe.throw(_("Date range cannot exceed {0} days.").format(MAX_RANGE_DAYS))

	return from_date, to_date


def _get_enabled_shift_doc(shift):
	shift_doc = frappe.get_doc("Shift Type", shift)
	if not shift_doc.enable_auto_attendance:
		frappe.throw(_("Enable Auto Attendance on this Shift Type first."))
	return shift_doc


def _unlinked_checkin_filters(employee: str, shift: str, attendance_date, linked_log=None) -> dict | None:
	"""Filters matching the still-unlinked checkin(s) for one shift occurrence.

	Deliberately does NOT filter on skip_auto_attendance. hrms core itself sets
	skip_auto_attendance=1 on a log when mark_attendance_and_link_log() hits the
	"already marked" exception (handle_attendance_exception ->
	skip_attendance_in_checkins in employee_checkin.py) — i.e. exactly when a
	late-syncing log shows up after this shift's Attendance already exists.
	That is the delayed-sync pattern this module targets, so most of the logs
	we need to find already carry skip_auto_attendance=1. Filtering it out here
	would exclude the very records this tool exists to fix.
	"""
	if linked_log is not None:
		if not linked_log.shift_start:
			return None
		# No time comparison here: the linked log isn't necessarily the
		# earlier one. A device can upload an OUT before its matching IN
		# syncs in later — the late-arriving log can have an earlier
		# clock time than the one that's already linked. What matters is
		# only that a second, still-unlinked log exists for the same
		# shift occurrence.
		return {
			"employee": employee,
			"shift": shift,
			"shift_start": linked_log.shift_start,
			"attendance": ["is", "not set"],
		}

	# No linked log to compare against — match by the shift occurrence
	# falling on this Attendance's date instead.
	return {
		"employee": employee,
		"shift": shift,
		"shift_start": ["between", [f"{attendance_date} 00:00:00", f"{attendance_date} 23:59:59"]],
		"attendance": ["is", "not set"],
	}


def has_submitted_payroll_period(employee: str, attendance_date) -> bool:
	"""True if a submitted Salary Slip already covers this employee/date,
	i.e. payroll for this period is finalized and Attendance should not be
	silently rewritten underneath it."""
	return bool(
		frappe.db.exists(
			"Salary Slip",
			{
				"employee": employee,
				"docstatus": 1,
				"start_date": ["<=", attendance_date],
				"end_date": [">=", attendance_date],
			},
		)
	)


@frappe.whitelist()
def get_broken_attendance_records(
	shift: str, from_date: str, to_date: str, employee: str | None = None, department: str | None = None
) -> list[dict]:
	"""Find Attendance records under this shift marked from incomplete
	Employee Checkin data (0 or 1 linked logs) while a checkin covering that
	same shift occurrence still sits unlinked — the delayed-sync pattern.

	Two cases, both caused by the same root problem (a log syncing in after
	the shift's auto-attendance run already happened):
	- 1 linked log: e.g. only the IN arrived in time, got marked Absent/
	  wrong hours off that single log; the OUT (or a further IN/OUT pair)
	  arrives later, unlinked.
	- 0 linked logs: the whole device/server was down for that shift, core's
	  mark_absent_for_dates_with_no_attendance() marked Absent with no
	  checkin involved at all; then both logs arrive late together.

	Read-only, used to populate the confirm-dialog preview.
	"""
	from_date, to_date = _validate_date_range(from_date, to_date)
	_get_enabled_shift_doc(shift)

	filters = {
		"shift": shift,
		"docstatus": 1,
		"attendance_date": ["between", [from_date, to_date]],
		"modify_half_day_status": ["!=", 1],
	}
	if employee:
		filters["employee"] = employee
	if department:
		filters["department"] = department

	candidates = frappe.get_all(
		"Attendance",
		filters=filters,
		fields=["name", "employee", "employee_name", "attendance_date", "status"],
	)

	results = []
	for att in candidates:
		linked_logs = frappe.get_all(
			"Employee Checkin",
			filters={"attendance": att.name},
			fields=["name", "time", "shift_start"],
		)
		if len(linked_logs) > 1:
			continue

		linked_log = linked_logs[0] if linked_logs else None
		unlinked_filters = _unlinked_checkin_filters(att.employee, shift, att.attendance_date, linked_log)

		if unlinked_filters is None or not frappe.db.exists("Employee Checkin", unlinked_filters):
			continue

		results.append(
			{
				"attendance": att.name,
				"employee": att.employee,
				"employee_name": att.employee_name,
				"attendance_date": att.attendance_date,
				"status": att.status,
				"linked_checkin_count": len(linked_logs),
				"payroll_locked": has_submitted_payroll_period(att.employee, att.attendance_date),
			}
		)

	return results


@frappe.whitelist()
def fix_delayed_sync_attendance(shift: str, attendance_names: list[str] | str) -> dict:
	"""Cancel the given broken Attendance records and reprocess the shift so
	they're recreated correctly from both checkins. HR Manager only.
	Validates every record before touching any of them — either all of the
	selected records get fixed, or none do.
	"""
	if "HR Manager" not in frappe.get_roles():
		frappe.throw(_("Only HR Manager can run this."), frappe.PermissionError)

	if isinstance(attendance_names, str):
		attendance_names = frappe.parse_json(attendance_names)

	if not attendance_names:
		frappe.throw(_("No records selected."))

	shift_doc = _get_enabled_shift_doc(shift)

	blocked = []
	checkins_to_unskip = set()
	for name in attendance_names:
		att = frappe.db.get_value(
			"Attendance",
			name,
			["employee", "employee_name", "attendance_date", "shift", "docstatus"],
			as_dict=True,
		)
		if not att or att.shift != shift or att.docstatus != 1:
			frappe.throw(_("{0} does not belong to shift {1}, or is not submitted.").format(name, shift))

		if has_submitted_payroll_period(att.employee, att.attendance_date):
			blocked.append(f"{name} ({att.employee_name or att.employee})")
			continue

		linked_logs = frappe.get_all(
			"Employee Checkin", filters={"attendance": name}, fields=["name", "shift_start"]
		)
		if len(linked_logs) not in (0, 1):
			frappe.throw(
				_(
					"{0} no longer matches the expected broken-record pattern (checkins changed). Re-scan and try again."
				).format(name)
			)

		linked_log = linked_logs[0] if linked_logs else None
		unlinked_filters = _unlinked_checkin_filters(att.employee, shift, att.attendance_date, linked_log)
		unlinked_names = (
			frappe.get_all("Employee Checkin", filters=unlinked_filters, pluck="name")
			if unlinked_filters
			else []
		)
		if not unlinked_names:
			frappe.throw(
				_(
					"{0} no longer matches the expected broken-record pattern (checkins changed). Re-scan and try again."
				).format(name)
			)

		checkins_to_unskip.update(unlinked_names)
		if linked_log:
			checkins_to_unskip.add(linked_log.name)

	if blocked:
		frappe.throw(
			_("Blocked — these fall inside an already-processed payroll period: {0}").format(
				", ".join(blocked)
			)
		)

	cancelled = 0
	for name in attendance_names:
		att = frappe.get_doc("Attendance", name)
		att.cancel()
		att.add_comment("Comment", _("Corrected — delayed biometric sync (frontier_hr)"))
		cancelled += 1

	if checkins_to_unskip:
		# Clear the skip flag hrms core set on these logs when it originally hit
		# the "already marked" exception (see _unlinked_checkin_filters), so
		# process_auto_attendance()'s get_employee_checkins() — which itself
		# filters on skip_auto_attendance=0 — actually picks them up below
		# instead of silently re-creating the same broken Attendance.
		frappe.db.set_value(
			"Employee Checkin", {"name": ["in", list(checkins_to_unskip)]}, "skip_auto_attendance", 0
		)

	reprocess_message = shift_doc.process_auto_attendance(is_manually_triggered=True)

	return {"cancelled": cancelled, "reprocess_message": reprocess_message}

