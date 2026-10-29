# Copyright (c) 2026, Frontier Softech
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class AttendanceLeaveAccrualPolicy(Document):
	def validate(self):
		self.validate_credits()
		self.validate_threshold()

	def validate_credits(self):
		"""A status listed twice would credit a day twice over."""
		seen = set()
		for row in self.credits:
			if row.attendance_status in seen:
				frappe.throw(
					_("Row #{0}: {1} is listed more than once.").format(row.idx, row.attendance_status)
				)
			seen.add(row.attendance_status)

		if not any(flt(row.credit) or flt(row.half_day_credit) for row in self.credits):
			frappe.throw(_("At least one status must earn credit, or the counter can never move."))

	def validate_threshold(self):
		"""A zero threshold would grant leave forever on the first credited day."""
		if flt(self.days_required) <= 0:
			frappe.throw(_("Days Required per Leave must be greater than zero."))
		if flt(self.leaves_granted) <= 0:
			frappe.throw(_("Leaves Granted must be greater than zero."))
