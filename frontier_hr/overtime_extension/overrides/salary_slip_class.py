"""Salary Slip class override — makes the pre-save preview show real numbers.

`fetch_ot_hours` / `fetch_working_hours` are registered on `before_validate`,
which only runs on save. The form's own preview path
(`get_emp_and_working_day_details` -> `make_salary_slip` ->
`process_salary_structure` -> `calculate_net_pay`) evaluates every salary
component formula BEFORE any save, so those fields are still empty there:

    custom_payroll_basis        the field default, not the employee's real shift
    custom_standard_day_hours   0
    custom_standard_month_hours 0
    custom_regular_working_hours 0

Two consequences, both seen on recode.com:

  * The hours formulas divided by zero. The formulas now carry an
    `if <divisor> else 0` guard, so that no longer raises — but it made the
    preview silently show 0.
  * Component CONDITIONS keyed on `custom_payroll_basis` matched the wrong
    branch, so a Working Hours employee's preview showed no basic row at all
    while the saved slip was correct. An HR user reviewing before saving would
    read that as a broken slip.

Populating the fields at the top of `process_salary_structure` fixes both:
dates are already resolved by then, and nothing has been evaluated yet.

Deliberately NOT hooked into `calculate_net_pay`: that runs many times per
slip, including from `sunday_deduction.overrides.apply_sunday_deduction` AFTER
it has subtracted the deducted Sundays from `custom_regular_working_hours`.
Re-running the fetch there would rebuild the hours from Attendance and silently
undo the Sunday deduction.
"""

from hrms.payroll.doctype.salary_slip.salary_slip import SalarySlip

from frontier_hr.overtime_extension.overrides.salary_slip import (
	fetch_ot_hours,
	fetch_working_hours,
)



def _apply_sunday_deduction(doc):
	"""Sunday Deduction Policy lives in company_hr_ext, which is not a
	required app of frontier_hr. Where it is absent this is a no-op, so the
	preview simply shows no Sunday deduction — the same figure the saved slip
	will carry, because the before_save hook is equally absent there."""
	try:
		from company_hr_ext.sunday_deduction.overrides import apply_sunday_deduction
	except ImportError:
		return

	apply_sunday_deduction(doc)


class CustomSalarySlip(SalarySlip):
	def process_salary_structure(self, for_preview=0, lwp_days_corrected=None):
		if self.payroll_frequency:
			self.get_date_details()

		fetch_ot_hours(self)
		fetch_working_hours(self)

		result = super().process_salary_structure(
			for_preview=for_preview, lwp_days_corrected=lwp_days_corrected
		)

		# Sunday deduction is registered on before_save/before_submit, so the
		# preview would otherwise show pre-deduction pay and the figure would
		# drop on save — 6,072.83 -> 5,533.39 on recode.com. Applying it here
		# makes preview match the saved slip.
		#
		# It cannot double-apply: process_salary_structure is only reached from
		# the preview mapping (salary_structure._make_salary_slip) and the
		# income-tax computation report. The save path runs validate() ->
		# calculate_net_pay() directly and never comes through here. The hook
		# also rebuilds custom_sunday_deductions from scratch on every call, so
		# repeated invocations converge rather than accumulate.
		_apply_sunday_deduction(self)

		return result
