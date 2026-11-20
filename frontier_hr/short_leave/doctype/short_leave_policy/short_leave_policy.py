# Copyright (c) 2026, Frontier Softech
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from frontier_hr.policy_scope import dedupe_shifts


class ShortLeavePolicy(Document):
	def validate(self):
		dedupe_shifts(self)
		self.validate_quota()

	def validate_quota(self):
		"""A zero either way makes the policy unusable rather than strict: no
		request could ever be saved against it, with no hint why."""
		if flt(self.hours_per_short_leave) <= 0:
			frappe.throw(_("Hours per Short Leave must be greater than zero."))
		if flt(self.short_leaves_per_period) <= 0:
			frappe.throw(_("Short Leaves per Period must be greater than zero."))
