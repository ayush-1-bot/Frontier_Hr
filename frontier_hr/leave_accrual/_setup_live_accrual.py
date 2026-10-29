"""Live accrual test bed — COMMITS, left in place.

Run: bench --site <site> execute frontier_hr.leave_accrual._setup_live_accrual.run
"""
import frappe
from frappe.utils import add_days, flt, getdate

POLICY = "Attendance Leave Accrual Policy"
NAME = "Frontier Attendance Leave Accrual"
SHIFT = "day"
LEAVE_TYPE = "Earned Leave"


def run():
	emp = frappe.db.get_value("Employee", {"employee_number": "_seed_deduction_demo",
		"employee_name": "Oscar Ontime"}, "name")
	company = frappe.db.get_value("Employee", emp, "company")

	if not frappe.db.exists("Leave Type", LEAVE_TYPE):
		frappe.get_doc({"doctype": "Leave Type", "leave_type_name": LEAVE_TYPE,
			"max_leaves_allowed": 0}).insert(ignore_permissions=True)

	if frappe.db.exists(POLICY, NAME):
		frappe.delete_doc(POLICY, NAME, force=True, ignore_permissions=True)
	frappe.clear_document_cache(POLICY, NAME)
	frappe.get_doc({
		"doctype": POLICY, "policy_name": NAME, "company": company, "shift_type": SHIFT,
		"days_required": 3, "leaves_granted": 1, "leave_type": LEAVE_TYPE,
		"reset_period": "Monthly", "carry_forward_remainder": 1, "carry_forward_counter": 1,
		"credits": [
			{"attendance_status": "Present", "credit": 1},
			{"attendance_status": "Half Day", "credit": 0.5},
			{"attendance_status": "Absent", "credit": 0},
		],
	}).insert(ignore_permissions=True)

	# clean slate for this employee on the test window
	start = getdate("2026-10-05")
	dates = [add_days(start, i) for i in range(4)]
	for name in frappe.get_all("Attendance", filters={"employee": emp,
			"attendance_date": ["between", [dates[0], dates[-1]]]}, pluck="name"):
		d = frappe.get_doc("Attendance", name)
		if d.docstatus == 1:
			d.flags.ignore_permissions = True
			d.cancel()
		d.delete(ignore_permissions=True, force=True)
	frappe.db.set_value("Employee", emp, {"custom_attendance_credit_days": 0,
		"custom_attendance_leaves_earned": 0, "custom_attendance_credit_period": None})

	def bal():
		return flt(frappe.db.sql("""SELECT SUM(leaves) FROM `tabLeave Ledger Entry`
			WHERE docstatus=1 AND employee=%s AND leave_type=%s""", (emp, LEAVE_TYPE))[0][0], 3)

	print(f"employee {emp}  threshold=3 days  leave={LEAVE_TYPE}")
	print(f"{'date':<12}{'status':<10}{'counter':>9}{'earned':>8}{'balance':>9}")
	for i, date in enumerate(dates):
		status = "Half Day" if i == 3 else "Present"
		doc = frappe.get_doc({"doctype": "Attendance", "employee": emp, "attendance_date": date,
			"status": status, "company": company, "shift": SHIFT}).insert(ignore_permissions=True)
		doc.submit()
		e = frappe.db.get_value("Employee", emp,
			["custom_attendance_credit_days", "custom_attendance_leaves_earned"], as_dict=True)
		print(f"{str(date):<12}{status:<10}{flt(e.custom_attendance_credit_days):>9}"
		      f"{flt(e.custom_attendance_leaves_earned):>8}{bal():>9}")

	frappe.db.commit()
	logs = frappe.get_all("Attendance Leave Grant Log", filters={"employee": emp},
		fields=["grant_date","leaves_granted","counter_before","counter_after","leave_allocation"])
	print("grant log:", logs)
