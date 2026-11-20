"""What an approved short leave does to the day's Attendance.

Two things, each a policy switch:

  excuse_late_and_early   the minutes the short leave covers are not counted
                          as late coming or early going, so the day is not
                          deducted for time the employee had permission to miss
  paid                    those hours are added back to the payable day, so
                          the employee is paid for them

Without the first, an approved short leave would be punished exactly like an
unapproved lateness -- the employee would spend a short leave AND lose pay for
the same two hours.
"""

import frappe
from frappe.utils import cint, flt, get_time, getdate

from frontier_hr.short_leave.api import approved_requests, resolve_policy

from datetime import datetime


def _overlap_minutes(start, end, window_start, window_end):
	"""Minutes two windows share. Zero when either is empty or they miss."""
	if not (start and end and window_start and window_end):
		return 0.0
	first = max(start, window_start)
	last = min(end, window_end)
	return max((last - first).total_seconds() / 60.0, 0.0)


def _policy_for(doc):
	if not doc.get("employee"):
		return None
	return resolve_policy(doc.employee, doc.get("attendance_date"), shift=doc.get("shift"))


def excused_split(doc, shift_start, shift_end, in_time, out_time):
	"""Minutes of late coming and early going an approved short leave covers.

	Takes the shift window already worked out by the caller rather than
	recomputing it: the deduction pass owns that calculation, night shifts
	included, and two copies of it would drift.
	"""
	policy = _policy_for(doc)
	if not policy or not cint(policy.excuse_late_and_early):
		return 0.0, 0.0

	late = early = 0.0
	date = getdate(doc.attendance_date)
	for row in approved_requests(doc.employee, date):
		start = datetime.combine(date, get_time(row.from_time))
		end = datetime.combine(date, get_time(row.to_time))
		# The late window is shift start up to arrival; the early window is
		# departure up to shift end. A short leave in the middle of the day
		# overlaps neither, and excuses nothing -- correctly, since the
		# employee was neither late nor gone early.
		late += _overlap_minutes(start, end, shift_start, in_time)
		early += _overlap_minutes(start, end, out_time, shift_end)

	return late, early


def credit_paid_hours(doc, method=None):
	"""Add paid short leave hours back to the day's payable hours.

	Runs between the overtime pass, which rebuilds custom_base_hours from the
	punches, and the deduction pass, which subtracts from it -- so the hours
	are there to be paid and still available for a deduction to take if the
	employee was late beyond what the short leave covers.

	Idempotent because the rebuild happens on every save.
	"""
	doc.custom_short_leave_hours = 0

	if doc.get("status") in ("Absent", "On Leave"):
		return

	policy = _policy_for(doc)
	if not policy:
		return

	hours = flt(sum(flt(row.hours) for row in approved_requests(doc.employee, doc.attendance_date)), 3)
	if not hours:
		return

	doc.custom_short_leave_hours = hours
	if not cint(policy.paid):
		return

	payable = flt(doc.get("custom_base_hours")) + hours
	standard = flt(doc.get("standard_working_hours"))
	# Never pay more than a full day: the short leave replaces hours that were
	# missed, it does not add to a day that was already complete.
	doc.custom_base_hours = min(payable, standard) if standard else payable
