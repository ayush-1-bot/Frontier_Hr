"""Dump the seeded Attendance rows. Run:
bench --site <site> execute frontier_hr.deduction_policy._report_demo.run
"""

import frappe

SEED_TAG = "_seed_deduction_demo"


def run():
	employees = frappe.get_all("Employee", filters={"employee_number": SEED_TAG}, fields=["name", "employee_name"])
	names = {e.name: e.employee_name for e in employees}

	rows = frappe.get_all(
		"Attendance",
		filters={"employee": ["in", list(names)], "docstatus": ["<", 2]},
		fields=[
			"name", "employee", "attendance_date", "status", "in_time", "out_time",
			"working_hours", "custom_base_hours", "custom_total_ot_hours",
			"late_entry", "early_exit", "docstatus",
		],
		order_by="employee, attendance_date",
	)

	header = f"{'employee':<18}{'date':<12}{'in':<9}{'out':<9}{'status':<10}{'work_h':>7}{'base_h':>8}{'ot_h':>6}  flags"
	print(header)
	print("-" * len(header))
	for r in rows:
		in_t = str(r.in_time)[11:16] if r.in_time else "--"
		out_t = str(r.out_time)[11:16] if r.out_time else "--"
		flags = ",".join(f for f, v in (("late", r.late_entry), ("early", r.early_exit)) if v) or "-"
		print(
			f"{names[r.employee]:<18}{str(r.attendance_date):<12}{in_t:<9}{out_t:<9}"
			f"{r.status:<10}{r.working_hours or 0:>7.2f}{r.custom_base_hours or 0:>8.2f}"
			f"{r.custom_total_ot_hours or 0:>6.2f}  {flags}"
		)
	print(f"\n{len(rows)} rows")
