"""Credit attendance that was submitted before the accrual hooks existed.

	bench --site <site> execute frontier_hr.leave_accrual.backfill.run
	bench --site <site> execute frontier_hr.leave_accrual.backfill.run --kwargs "{'dry_run': 0}"

Dry run by default: it reports what each day would credit and changes nothing.

Safe to re-run. `accrue_on_submit` skips a row whose credit already matches
`custom_accrual_credit_days`, so a second pass over the same days is a no-op
rather than a second helping of leave.

Rows are walked oldest first so grants land on the day that actually earned
them, and the per-period reset sees the months in order.
"""

import frappe
from frappe.utils import flt

from frontier_hr.leave_accrual.overrides.attendance import (
	_credit_for,
	_resolve_policy,
	accrue_on_submit,
)


def run(from_date=None, to_date=None, employee=None, company=None, dry_run=1):
	dry_run = int(dry_run)

	filters = {"docstatus": 1}
	if from_date and to_date:
		filters["attendance_date"] = ["between", [from_date, to_date]]
	elif from_date:
		filters["attendance_date"] = [">=", from_date]
	elif to_date:
		filters["attendance_date"] = ["<=", to_date]
	if employee:
		filters["employee"] = employee
	if company:
		filters["company"] = company

	rows = frappe.get_all(
		"Attendance",
		filters=filters,
		fields=["name", "employee", "employee_name", "attendance_date", "status",
		        "custom_accrual_credit_days"],
		order_by="attendance_date asc, employee asc",
	)

	seen, credited, skipped = {}, 0, 0
	for row in rows:
		doc = frappe.get_doc("Attendance", row.name)
		policy = _resolve_policy(doc)
		if not policy:
			skipped += 1
			continue

		credit = _credit_for(doc, policy)
		if abs(credit - flt(row.custom_accrual_credit_days)) < 0.0001:
			skipped += 1  # already counted, or the status earns nothing
			continue

		if not dry_run:
			accrue_on_submit(doc)
		seen.setdefault(row.employee_name, 0)
		seen[row.employee_name] += credit
		credited += 1

	if not dry_run:
		frappe.db.commit()

	print(f"{'DRY RUN - nothing changed' if dry_run else 'APPLIED'}")
	print(f"attendance scanned : {len(rows)}")
	print(f"credited           : {credited}")
	print(f"skipped            : {skipped}  (no policy, or already counted)")
	for name, days in sorted(seen.items()):
		print(f"   {name:<18} +{flt(days, 3)} days")

	if not dry_run:
		print("\nresulting counters:")
		for emp in frappe.get_all(
			"Employee",
			filters={"custom_attendance_accrual_policy": ["is", "set"]},
			fields=["employee_name", "custom_attendance_credit_days",
			        "custom_attendance_leaves_earned"],
		):
			print(f"   {emp.employee_name:<18} counter="
			      f"{flt(emp.custom_attendance_credit_days, 3):<8}"
			      f"earned={flt(emp.custom_attendance_leaves_earned, 3)}")
