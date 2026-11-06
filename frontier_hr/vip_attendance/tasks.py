"""
VIP attendance
--------------
Employees flagged `custom_exempt_from_attendance` are not expected to punch.
A daily job writes the IN/OUT checkins their shift implies and marks the day
Present, so the biometric gap does not read as absence on their payroll.

Skips a day that is on the employee's holiday list, and a weekend where no
holiday list is set. Anything already punched or already marked is left alone,
so running the job twice costs nothing.

The Attendance it submits goes through the same hooks as any other, so a VIP
earns leave accrual on the day and is judged by the deduction policy -- which
charges nothing, because the punches it writes sit exactly on the shift bounds.
"""

import frappe
from frappe.utils import getdate, today


def auto_mark_vip_attendance(date_str=None):
    """Auto-mark Present + create IN/OUT checkins for exempt employees."""
    date = getdate(date_str) if date_str else getdate(today())
    frappe.logger().info(f"VIP Attendance: Running for {date}")

    exempt_employees = frappe.get_all(
        "Employee",
        filters={
            "custom_exempt_from_attendance": 1,
            "status": "Active"
        },
        fields=["name", "employee_name", "holiday_list", "company"]
    )

    if not exempt_employees:
        frappe.logger().info("VIP Attendance: No exempt employees found.")
        return

    for emp in exempt_employees:
        try:
            holiday_list = emp.holiday_list or frappe.db.get_value(
                "Company", emp.company, "default_holiday_list"
            )

            if holiday_list:
                is_holiday = frappe.db.exists("Holiday", {
                    "parent": holiday_list,
                    "holiday_date": date
                })
                if is_holiday:
                    frappe.logger().info(f"VIP: Skipping {emp.name} — holiday")
                    continue
            else:
                if date.weekday() in (5, 6):
                    frappe.logger().info(f"VIP: Skipping {emp.name} — weekend, no holiday list")
                    continue

            existing_attendance = frappe.db.get_value(
                "Attendance",
                {
                    "employee": emp.name,
                    "attendance_date": date,
                    "docstatus": ["!=", 2]
                },
                "name"
            )

            existing_in = frappe.db.exists("Employee Checkin", {
                "employee": emp.name,
                "log_type": "IN",
                "time": ["between", [f"{date} 00:00:00", f"{date} 23:59:59"]]
            })

            existing_out = frappe.db.exists("Employee Checkin", {
                "employee": emp.name,
                "log_type": "OUT",
                "time": ["between", [f"{date} 00:00:00", f"{date} 23:59:59"]]
            })

            if existing_attendance and existing_in and existing_out:
                frappe.logger().info(f"VIP: Skipping {emp.name} — fully punched")
                continue

            shift_assignment = frappe.db.get_value(
                "Shift Assignment",
                filters={
                    "employee": emp.name,
                    "start_date": ["<=", date],
                    "docstatus": 1,
                    "status": "Active"
                },
                fieldname="shift_type",
                order_by="start_date desc"
            )

            if not shift_assignment:
                shift_assignment = frappe.db.get_value(
                    "Employee", emp.name, "default_shift"
                )

            shift_in = None
            shift_out = None

            if shift_assignment:
                shift = frappe.get_doc("Shift Type", shift_assignment)
                shift_in = str(shift.start_time)
                shift_out = str(shift.end_time)
            else:
                frappe.logger().info(
                    f"VIP: No shift found for {emp.name} — marking Present only"
                )

            if shift_in and shift_out:
                if not existing_in:
                    cin = frappe.new_doc("Employee Checkin")
                    cin.employee = emp.name
                    cin.log_type = "IN"
                    cin.time = f"{date} {shift_in}"
                    cin.shift = shift_assignment
                    cin.latitude = 0
                    cin.longitude = 0
                    cin.flags.ignore_permissions = True
                    cin.insert()
                    frappe.logger().info(f"VIP: Created IN for {emp.employee_name}")

                if not existing_out:
                    cout = frappe.new_doc("Employee Checkin")
                    cout.employee = emp.name
                    cout.log_type = "OUT"
                    cout.time = f"{date} {shift_out}"
                    cout.shift = shift_assignment
                    cout.latitude = 0
                    cout.longitude = 0
                    cout.flags.ignore_permissions = True
                    cout.insert()
                    frappe.logger().info(f"VIP: Created OUT for {emp.employee_name}")

            if not existing_attendance:
                att = frappe.new_doc("Attendance")
                att.employee = emp.name
                att.attendance_date = date
                att.status = "Present"
                att.company = emp.company
                if shift_assignment:
                    att.shift = shift_assignment
                att.flags.ignore_permissions = True
                att.insert()

                if shift_in and shift_out:
                    frappe.db.set_value("Attendance", att.name, {
                        "in_time": f"{date} {shift_in}",
                        "out_time": f"{date} {shift_out}",
                    }, update_modified=False)

                att.reload()
                att.submit()
                frappe.logger().info(
                    f"VIP: Marked Present for {emp.employee_name} ({emp.name})"
                )

        except Exception as e:
            frappe.logger().error(f"VIP failed for {emp.name}: {str(e)}")
            continue

    frappe.db.commit()
    frappe.logger().info(f"VIP Attendance: Completed for {date}")