"""Nearest-shift pick and automatic Log Type for employees with several shifts a day.

Toggle: HR Settings -> Pick Nearest Shift on Check-in (custom_pick_nearest_shift).

Standard hrms cuts overlapping shift windows at the earlier shift's end, so a
late OUT at 13:10 for a shift ending 13:00 lands on the next shift. It also
never derives Log Type from the shift. This override fixes both in fetch_shift,
which validate, the form's Fetch Shift button and the bulk list action all call.

Known limits:
- A missed OUT can label the next punch OUT while the old shift's window is open.
- A punch synced after newer punches were saved does not relabel those newer logs.
"""

import frappe
from frappe.utils import get_datetime

from hrms.hr.doctype.employee_checkin.employee_checkin import EmployeeCheckin
from hrms.hr.doctype.shift_assignment.shift_assignment import (
	_is_shift_outside_assignment_period,
	get_shift_details,
	get_shifts_for_date,
)


def pick_shift(candidates: list[dict], time, log_type: str | None, last_log: dict | None) -> tuple:
	"""Return (shift, log_type) for a punch that falls inside the candidates' windows."""
	to_start = lambda shift: abs((time - shift.start_datetime).total_seconds())  # noqa: E731
	to_end = lambda shift: abs((time - shift.end_datetime).total_seconds())  # noqa: E731

	if log_type == "OUT":
		return min(candidates, key=to_end), "OUT"

	nearest_start = min(candidates, key=to_start)
	if log_type == "IN":
		return nearest_start, "IN"

	if last_log and last_log.log_type == "IN":
		open_shift = next((s for s in candidates if s.start_datetime == last_log.shift_start), None)
		others = [s for s in candidates if s is not open_shift]
		if open_shift and (not others or to_end(open_shift) <= min(map(to_start, others))):
			return open_shift, "OUT"

	return nearest_start, "IN"


class CustomEmployeeCheckin(EmployeeCheckin):
	@frappe.whitelist()
	def fetch_shift(self):
		if self.attendance or not frappe.db.get_single_value("HR Settings", "custom_pick_nearest_shift"):
			return super().fetch_shift()

		time = get_datetime(self.time)
		candidates = self._shifts_containing(time)
		if not candidates:
			return super().fetch_shift()

		shift, self.log_type = pick_shift(candidates, time, self.log_type, self._last_log(time))
		self.offshift = 0
		self.shift = shift.shift_type.name
		self.shift_start = shift.start_datetime
		self.shift_end = shift.end_datetime
		self.shift_actual_start = shift.actual_start
		self.shift_actual_end = shift.actual_end
		self.overtime_type = shift.overtime_type

	def _shifts_containing(self, time) -> list[dict]:
		found = []
		for assignment in get_shifts_for_date(self.employee, time):
			details = get_shift_details(assignment.shift_type, for_timestamp=time)
			details.overtime_type = assignment.overtime_type or None
			if (
				not _is_shift_outside_assignment_period(details, assignment)
				and details.actual_start <= time <= details.actual_end
			):
				found.append(details)
		return found

	def _last_log(self, time) -> dict | None:
		return frappe.db.get_value(
			"Employee Checkin",
			{"employee": self.employee, "time": ("<", time)},
			["log_type", "shift_start"],
			order_by="time desc",
			as_dict=True,
		)
