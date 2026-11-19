# Copyright (c) 2026, Frontier Softech
# For license information, please see license.txt

from frappe.model.document import Document


class AttendancePolicyShift(Document):
	"""One shift a policy applies to.

	Shared by both attendance policies: a Table MultiSelect needs a child
	doctype with a single Link field, and both policies pin shifts the same
	way, so a second identical doctype would only have to be kept in step.
	"""
	pass
