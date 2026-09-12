from hrms.payroll.doctype.salary_structure_assignment.salary_structure_assignment import (
    SalaryStructureAssignment,
)


class CustomSalaryStructureAssignment(SalaryStructureAssignment):
    def _get_component_eval_context(self):
        data = super()._get_component_eval_context()
        # Salary Slip OT custom fields are not in the SSA eval context by default.
        # Inject their neutral defaults so formulas that reference them don't raise
        # NameError during assignment-time validation / CTC preview.
        data.setdefault("custom_standard_multiplier", 2.0)
        data.setdefault("custom_actual_ot_hours", 0.0)
        data.setdefault("custom_payable_ot_hours", 0.0)
        data.setdefault("gross_pay", 0.0)
        # Payroll-basis hours (see README section 11). Both default to 0 so that
        # at assignment time the payment-days Basic wins its
        # `custom_regular_working_hours == 0` condition and the hours-based
        # component stays out of the CTC figure — a structure assignment has no
        # period, so there are no hours to price yet.
        data.setdefault("custom_regular_working_hours", 0.0)
        data.setdefault("custom_total_working_hours", 0.0)
        data.setdefault("custom_holiday_hours", 0.0)
        # Non-zero on purpose: it is a divisor in the hourly-rate formulas, and a
        # 0 here would raise ZeroDivisionError during assignment-time evaluation.
        # The value itself is irrelevant at this point — with 0 hours and 0 OT
        # both hourly components evaluate to 0 regardless.
        from frontier_hr.overtime_extension.overrides.salary_slip import (
            DEFAULT_STANDARD_DAY_HOURS,
            DEFAULT_WORKING_DAYS,
            payroll_basis_for,
            shift_on_date,
        )

        data.setdefault("custom_standard_day_hours", DEFAULT_STANDARD_DAY_HOURS)
        # Also a divisor — same ZeroDivisionError guard, nominal full month.
        data.setdefault(
            "custom_standard_month_hours", DEFAULT_WORKING_DAYS * DEFAULT_STANDARD_DAY_HOURS
        )

        # Resolved for real, not defaulted: the assignment pre-evaluates every
        # salary component and DROPS any whose condition is falsey, before the
        # slip ever sees it. Basis is a property of the employee's shift and
        # needs no payroll period, so resolving it here lets components carry a
        # condition on it and still survive that filter for the right employees.
        data.setdefault(
            "custom_payroll_basis", payroll_basis_for(shift_on_date(self.employee, self.from_date))
        )
        return data
