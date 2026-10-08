# Copyright (c) 2026, Frontier Softech
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class LateComingandEarlyOutDeductionPolicy(Document):
	def validate(self):
		self.validate_slabs()
		self.validate_priority()

	def validate_slabs(self):
		"""Reject overlapping or reversed ranges: they would make the deduction
		depend on row order rather than on how late the employee was."""
		# Ignored when the deduction is the actual minutes, so a stale table left
		# from the other mode must not block the save.
		if self.deduction_basis == "Actual Late Minutes":
			return

		for row in self.deduction_slabs:
			if row.to_minutes and row.to_minutes < row.from_minutes:
				frappe.throw(
					_("Row #{0}: To (Minutes) must not be less than From (Minutes).").format(row.idx)
				)

		for scope in ("Late Coming", "Early Out"):
			# A "Both" row competes with the specific rows of either kind, so it
			# is checked inside each scope rather than as a third group.
			rows = [r for r in self.deduction_slabs if r.applies_to in (scope, "Both")]
			rows.sort(key=lambda r: r.from_minutes)
			for earlier, later in zip(rows, rows[1:]):
				# to_minutes 0 means "no upper bound", so nothing may follow it.
				if not earlier.to_minutes or later.from_minutes <= earlier.to_minutes:
					frappe.throw(
						_("Rows #{0} and #{1} overlap for {2}. Ranges must not overlap.").format(
							earlier.idx, later.idx, _(scope)
						)
					)

	def validate_priority(self):
		"""A leave row with a zero conversion rate would consume balance without
		covering any minutes, looping the deduction walk on a row that can never
		be exhausted."""
		for row in self.deduction_priority:
			if row.source != "Leave":
				continue
			if flt(row.hours_per_unit) <= 0 or flt(row.days_per_unit) <= 0:
				frappe.throw(
					_("Row #{0}: Hours per Unit and Leave Days per Unit must both be greater than zero.").format(
						row.idx
					)
				)

		working_hours_rows = [r for r in self.deduction_priority if r.source == "Working Hours"]
		if len(working_hours_rows) > 1:
			frappe.throw(_("Only one Working Hours row is allowed — it always absorbs the remainder."))
		if working_hours_rows and working_hours_rows[0].idx != len(self.deduction_priority):
			frappe.throw(_("The Working Hours row must be last: no row after it can ever be reached."))
