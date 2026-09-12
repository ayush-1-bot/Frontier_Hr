import frappe
from frappe.model.document import Document
from frappe.utils import flt, getdate


class OTSlipBatch(Document):
    def validate(self):
        if getdate(self.start_date) > getdate(self.end_date):
            frappe.throw("Start Date must be before End Date")


@frappe.whitelist()
def preview_employees(batch_name):
    """Fetch eligible employees with OT in period."""
    batch = frappe.get_doc("OT Slip Batch", batch_name)

    filters = {"status": "Active", "company": batch.company}
    if batch.department:
        filters["department"] = batch.department
    if batch.branch:
        filters["branch"] = batch.branch

    employees = frappe.get_all("Employee", filters=filters, fields=["name", "employee_name"])

    # Clear existing rows
    batch.employees = []

    total_actual = 0.0
    eligible_count = 0

    for emp in employees:
        # Check OT hours
        actual_ot = frappe.db.sql("""
            SELECT COALESCE(SUM(custom_total_ot_hours), 0)
            FROM `tabAttendance`
            WHERE employee = %(emp)s
            AND attendance_date BETWEEN %(start)s AND %(end)s
            AND docstatus = 1
        """, {"emp": emp.name, "start": batch.start_date, "end": batch.end_date})[0][0]

        actual_ot = flt(actual_ot)

        # Check existing slip
        existing_slip = frappe.db.exists("Overtime Slip", {
            "employee": emp.name,
            "start_date": batch.start_date,
            "end_date": batch.end_date,
            "docstatus": ["!=", 2]
        })

        # Determine status + reason
        if existing_slip:
            slip_status = "Skipped"
            skip_reason = f"Slip exists: {existing_slip}"
        elif actual_ot <= 0:
            slip_status = "Skipped"
            skip_reason = "No OT hours"
        else:
            slip_status = "Pending"
            skip_reason = ""
            eligible_count += 1
            total_actual += actual_ot

        batch.append("employees", {
            "employee": emp.name,
            "employee_name": emp.employee_name,
            "actual_ot_hours": actual_ot,
            "slip_status": slip_status,
            "skip_reason": skip_reason
        })

    batch.total_employees = len(batch.employees)
    batch.total_actual_hours = total_actual
    batch.status = "Previewed"
    batch.save(ignore_permissions=True)

    return {
        "total": len(batch.employees),
        "eligible": eligible_count,
        "total_hours": total_actual
    }


# @frappe.whitelist()
# def create_slips(batch_name):
#     """Create OT slips for all eligible employees."""
#     batch = frappe.get_doc("OT Slip Batch", batch_name)
#     batch.status = "Processing"
#     batch.save(ignore_permissions=True)

#     created = 0
#     skipped = 0
#     failed = 0

#     for row in batch.employees:
#         if row.slip_status != "Pending":
#             skipped += 1
#             continue

#         try:
#             slip = _create_single_slip(row.employee, batch.start_date, batch.end_date, batch.company)
#             if slip:
#                 row.slip_status = "Created"
#                 row.slip_name = slip.name
#                 created += 1
#             else:
#                 row.slip_status = "Skipped"
#                 row.skip_reason = "No OT records"
#                 skipped += 1
#         except Exception as e:
#             row.slip_status = "Failed"
#             row.skip_reason = str(e)[:140]
#             failed += 1
#             frappe.log_error(frappe.get_traceback(), f"OT Bulk: {row.employee}")

#     batch.total_created = created
#     batch.total_skipped = skipped
#     batch.status = "Partial Failure" if failed else "Completed"
#     batch.save(ignore_permissions=True)
#     frappe.db.commit()

#     return {"created": created, "skipped": skipped, "failed": failed}
@frappe.whitelist()
def create_slips(batch_name):
    """Create OT slips for all eligible employees."""
    batch = frappe.get_doc("OT Slip Batch", batch_name)
    batch.status = "Processing"
    batch.save(ignore_permissions=True)

    created = 0
    skipped = 0
    failed = 0

    for row in batch.employees:
        if row.slip_status != "Pending":
            skipped += 1
            continue

        try:
            slip = _create_single_slip(row.employee, batch.start_date, batch.end_date, batch.company)
            
            # Check if slip was actually created
            if slip and slip.name:
                row.slip_status = "Created"
                row.slip_name = slip.name
                created += 1
            else:
                row.slip_status = "Skipped"
                row.skip_reason = "No OT records found"
                skipped += 1
                
        except Exception as e:
            # Check if slip got created anyway despite exception
            existing = frappe.db.exists("Overtime Slip", {
                "employee": row.employee,
                "start_date": batch.start_date,
                "end_date": batch.end_date,
                "docstatus": ["!=", 2]
            })
            if existing:
                row.slip_status = "Created"
                row.slip_name = existing
                row.skip_reason = f"Created with warning: {str(e)[:100]}"
                created += 1
            else:
                row.slip_status = "Failed"
                row.skip_reason = str(e)[:140]
                failed += 1
                frappe.log_error(frappe.get_traceback(), f"OT Bulk: {row.employee}")

    batch.total_created = created
    batch.total_skipped = skipped
    batch.status = "Partial Failure" if failed else "Completed"
    batch.save(ignore_permissions=True)
    frappe.db.commit()

    return {"created": created, "skipped": skipped, "failed": failed}

@frappe.whitelist()
def _create_single_slip(employee, start_date, end_date, company):
    """Create one OT slip for one employee."""
    records = frappe.get_all("Attendance",
        filters={
            "employee": employee,
            "attendance_date": ["between", [start_date, end_date]],
            "docstatus": 1,
            "custom_total_ot_hours": [">", 0]
        },
        fields=["name", "attendance_date", "custom_total_ot_hours",
                "overtime_type", "standard_working_hours"]
    )

    if not records:
        return None

    slip = frappe.new_doc("Overtime Slip")
    slip.employee     = employee
    slip.start_date   = start_date
    slip.end_date     = end_date
    slip.company      = company
    slip.posting_date = end_date

    for r in records:
        slip.append("overtime_details", {
            "reference_document":     r.name,
            "date":                   r.attendance_date,
            "overtime_type":          r.overtime_type or "ot",
            "overtime_duration":      r.custom_total_ot_hours,
            "standard_working_hours": r.standard_working_hours or 9.0,
        })

    slip.flags.ignore_permissions = True
    slip.insert()
    slip.submit()
    return slip


@frappe.whitelist()
def cancel_batch_slips(batch_name):
    """Cancel all submitted slips in this batch."""
    batch = frappe.get_doc("OT Slip Batch", batch_name)
    cancelled = 0

    for row in batch.employees:
        if row.slip_status == "Created" and row.slip_name:
            try:
                slip = frappe.get_doc("Overtime Slip", row.slip_name)
                if slip.docstatus == 1:
                    slip.cancel()
                row.slip_status = "Cancelled"
                cancelled += 1
            except Exception as e:
                frappe.log_error(str(e), f"OT Bulk Cancel: {row.slip_name}")

    batch.save(ignore_permissions=True)
    frappe.db.commit()
    return {"cancelled": cancelled}
