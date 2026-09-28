"""Show what the deduction policy did. Run:
bench --site <site> execute frontier_hr.deduction_policy._report_live.run
"""
import frappe

def run():
	emps = {e.name: e.employee_name for e in frappe.get_all("Employee",
		filters={"employee_number": "_seed_deduction_demo"}, fields=["name","employee_name"])}
	rows = frappe.get_all("Attendance",
		filters={"employee": ["in", list(emps)], "attendance_date": ["between",["2026-09-21","2026-09-24"]]},
		fields=["name","employee","attendance_date","status","in_time","out_time","working_hours",
		        "custom_base_hours","custom_late_coming_minutes","custom_early_out_minutes",
		        "custom_deduction_minutes","custom_deducted_leave_days","custom_deducted_hours",
		        "custom_deduction_policy","docstatus"],
		order_by="employee, attendance_date")
	h = (f"{'employee':<15}{'date':<12}{'in':<7}{'out':<7}{'late':>5}{'early':>6}"
	     f"{'ded_m':>7}{'lv_days':>9}{'ded_h':>7}{'base_h':>8}  breakup")
	print(h); print("-"*len(h))
	for r in rows:
		br = frappe.get_all("Attendance Deduction Entry",
			filters={"parent": r.name}, fields=["source","leave_type","minutes","leave_days","hours"],
			order_by="idx")
		trail = " + ".join(
			f"{(b.leave_type or b.source)}:{b.minutes}m"
			+ (f"/{b.leave_days}d" if b.leave_days else "")
			+ (f"/{b.hours}h" if b.hours else "") for b in br) or "-"
		print(f"{emps[r.employee]:<15}{str(r.attendance_date):<12}"
		      f"{str(r.in_time)[11:16]:<7}{str(r.out_time)[11:16]:<7}"
		      f"{r.custom_late_coming_minutes or 0:>5}{r.custom_early_out_minutes or 0:>6}"
		      f"{r.custom_deduction_minutes or 0:>7}{r.custom_deducted_leave_days or 0:>9}"
		      f"{r.custom_deducted_hours or 0:>7}{r.custom_base_hours or 0:>8.2f}  {trail}")
	print(f"\n{len(rows)} rows | policy stamped on {sum(1 for r in rows if r.custom_deduction_policy)}")
	led = frappe.db.sql("""SELECT leave_type, SUM(leaves) FROM `tabLeave Ledger Entry`
		WHERE transaction_type='Attendance' AND docstatus=1 GROUP BY leave_type""")
	print("leave debited from attendance:", led or "none")
