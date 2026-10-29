"""Make hrms count the leave this app debits from Attendance.

hrms sums consumed leave in `get_leaves_for_period`, whose branch chain credits
only `Leave Encashment`, expired `Leave Allocation` and `Leave Application`. Our
rows carry `transaction_type = "Attendance"`, so the query fetches them and then
every branch skips them: the ledger holds the debit but no hrms balance, report
or dashboard ever shows it.

The entries cannot be written as one of those three types instead —
`transaction_name` is a Dynamic Link and would have to point at a real document
of that doctype, which does not exist for a deduction. So the summation is
wrapped here rather than the data being bent to fit.

The wrapper only ADDS the Attendance rows hrms skipped; everything else is the
untouched original. Applied to each module that holds its own reference, since
two of them bind the function at import.
"""

import functools

import frappe
from frappe.utils import flt

# Modules binding `get_leaves_for_period` by name. leave_application itself
# resolves it as a module global, so patching its attribute covers the calls
# inside it too; the other two did `from ... import` at load.
CONSUMERS = (
	"hrms.hr.doctype.leave_application.leave_application",
	"hrms.hr.doctype.leave_encashment.leave_encashment",
	"hrms.hr.report.employee_leave_balance.employee_leave_balance",
)

FLAG = "_frontier_hr_counts_attendance"


def attendance_leaves(employee, leave_type, from_date, to_date):
	"""Leave this app debited in the window, as a negative number — the sign
	hrms uses for consumed leave, so it can simply be added."""
	if not (employee and leave_type and from_date and to_date):
		return 0.0

	total = frappe.db.sql(
		"""
		SELECT SUM(leaves) FROM `tabLeave Ledger Entry`
		WHERE docstatus = 1
		  AND employee = %(employee)s
		  AND leave_type = %(leave_type)s
		  AND transaction_type = 'Attendance'
		  AND is_expired = 0
		  AND from_date BETWEEN %(from_date)s AND %(to_date)s
		""",
		{"employee": employee, "leave_type": leave_type, "from_date": from_date, "to_date": to_date},
	)[0][0]
	return flt(total)


def apply():
	"""Idempotent: re-importing or re-running must not stack wrappers."""
	from hrms.hr.doctype.leave_application import leave_application

	original = leave_application.get_leaves_for_period
	if getattr(original, FLAG, False):
		return

	@functools.wraps(original)
	def get_leaves_for_period(employee, leave_type, from_date, to_date, skip_expired_leaves=True):
		taken = original(employee, leave_type, from_date, to_date, skip_expired_leaves)
		return flt(taken) + attendance_leaves(employee, leave_type, from_date, to_date)

	setattr(get_leaves_for_period, FLAG, True)

	for path in CONSUMERS:
		try:
			module = frappe.get_module(path)
		except Exception:
			# An hrms layout change must never stop the app from loading; the
			# balance is then simply as stale as it was before this wrapper.
			continue
		if hasattr(module, "get_leaves_for_period"):
			module.get_leaves_for_period = get_leaves_for_period
