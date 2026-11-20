# Copyright (c) 2026, Frontier Softech
# For license information, please see license.txt

from datetime import datetime

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_time, getdate

from frontier_hr.short_leave.api import get_balance, resolve_policy, units_for


class ShortLeaveRequest(Document):
	# Roles trusted to file on someone else's behalf.
	HR_ROLES = {"HR Manager", "HR User", "System Manager"}

	def validate(self):
		self.validate_employee()
		self.validate_own_request()
		self.set_policy()
		self.set_hours()
		self.validate_against_policy()
		self.validate_working_day()
		self.validate_overlap()
		self.validate_balance()

	def on_submit(self):
		self.resync_day()

	def on_cancel(self):
		self.resync_day()

	# -----------------------------------------------------------------

	def window(self):
		"""From and to as datetimes on this request's own date."""
		date = getdate(self.date)
		return (
			datetime.combine(date, get_time(self.from_time)),
			datetime.combine(date, get_time(self.to_time)),
		)

	def validate_employee(self):
		from hrms.hr.utils import validate_active_employee

		validate_active_employee(self.employee)

	def validate_own_request(self):
		"""An employee may only file for themselves.

		The Employee link lists every employee and the doctype is readable
		if_owner, so without this anyone with the Employee role could spend a
		colleague's quota -- and the colleague would never see the request.
		"""
		if frappe.session.user == "Administrator":
			return
		if self.HR_ROLES & set(frappe.get_roles()):
			return

		own = frappe.db.get_value("Employee", {"user_id": frappe.session.user, "status": "Active"}, "name")
		if not own:
			frappe.throw(_("No active Employee record is linked to your user."))
		if self.employee != own:
			frappe.throw(_("You can only request a short leave for yourself."))


	def set_policy(self):
		policy = resolve_policy(self.employee, self.date)
		if not policy:
			frappe.throw(
				_("No Short Leave Policy covers {0} on {1}.").format(
					frappe.bold(self.employee_name or self.employee), frappe.bold(self.date)
				)
			)
		self.policy = policy.name
		self._policy = policy

	def set_hours(self):
		start, end = self.window()
		if end <= start:
			# A short leave is time off inside one working day; a window that
			# wraps midnight would be a full day's leave, not this.
			frappe.throw(_("To Time must be later than From Time."))
		self.hours = flt((end - start).total_seconds() / 3600.0, 2)
		self.units_consumed = flt(units_for(self._policy, self.hours), 3)

	def validate_against_policy(self):
		longest = flt(self._policy.hours_per_short_leave)
		if self.hours > longest + 0.0001:
			frappe.throw(
				_("A short leave may not be longer than {0} hours. This one is {1}.").format(
					frappe.bold(longest), frappe.bold(self.hours)
				)
			)

	def validate_working_day(self):
		"""A holiday has nothing to be excused from, and spending the quota on
		one would cost the employee a short leave for a day they were already
		free."""
		from hrms.hr.utils import get_holiday_dates_for_employee

		if getdate(self.date) in set(
			getdate(d) for d in get_holiday_dates_for_employee(self.employee, self.date, self.date)
		):
			frappe.throw(_("{0} is a holiday for this employee.").format(frappe.bold(self.date)))

	def validate_overlap(self):
		"""Two requests covering the same minute would be excused twice and
		charged twice."""
		start, end = self.window()
		others = frappe.get_all(
			self.doctype,
			filters={
				"employee": self.employee,
				"date": getdate(self.date),
				"docstatus": ["<", 2],
				"name": ["!=", self.name or ""],
			},
			fields=["name", "from_time", "to_time"],
		)
		date = getdate(self.date)
		for row in others:
			other_start = datetime.combine(date, get_time(row.from_time))
			other_end = datetime.combine(date, get_time(row.to_time))
			if start < other_end and other_start < end:
				frappe.throw(
					_("Overlaps {0}, which already covers part of that time.").format(
						frappe.bold(row.name)
					)
				)

	def validate_balance(self):
		"""Checked on every save, so it is re-checked on submit too: two drafts
		cannot both slip through on a quota that only fits one."""
		balance = get_balance(self.employee, self.date, self._policy, ignore_request=self.name)
		if flt(self.units_consumed) > balance.remaining + 0.0001:
			frappe.throw(
				_(
					"Only {0} of {1} short leaves are left between {2} and {3}. This request needs {4}."
				).format(
					frappe.bold(flt(balance.remaining, 3)),
					frappe.bold(flt(balance.allowed, 2)),
					frappe.bold(balance.period_start),
					frappe.bold(balance.period_end),
					frappe.bold(flt(self.units_consumed, 3)),
				)
			)

	def resync_day(self):
		"""Recompute the day's Attendance, which may now owe a smaller
		deduction or a larger payable day. The Attendance is usually already
		submitted, so this goes through the same resync a Leave Application
		uses rather than the ORM."""
		from frontier_hr.deduction_policy.overrides.attendance import resync_attendance_range

		resync_attendance_range(
			frappe._dict(employee=self.employee, from_date=self.date, to_date=self.date)
		)
