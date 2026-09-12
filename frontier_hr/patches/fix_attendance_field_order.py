"""Pin the Attendance custom-field layout so every site renders it the same.

Third companion to fix_overtime_type_field_order (Overtime Type),
fix_salary_slip_field_order and fix_shift_type_field_order. Attendance was the
worst of the four on recode.com:

  * two fields at idx 0 and two more at idx 29 — four-way collision
  * custom_early_ot_minutes and custom_late_ot_minutes pointed insert_after at
    `custom_ot_section` / `custom_ot_col_break`, which are SALARY SLIP fields
    and do not exist on Attendance at all — dangling anchors that fall back to
    idx ordering
  * a `field_order` Property Setter from Customize Form overriding all of it

Result: six fields stacked into the right-hand column of the native Overtime
section, below Standard Working Hours.

Layout produced, anchored on `standard_working_hours` (last native field, so
nothing native is split):

    Overtime                    [native section, untouched]
        Overtime Type           | Standard Working Hours
        Actual Overtime Duration|

    Payroll Hours               [custom section, ALWAYS visible]
        Base Hours

    Overtime Detail             [custom section, hidden unless overtime_type]
        Total OT Hours          | Early OT (Minutes) | OT Status
                                | Late OT (Minutes)  |

Base Hours is split out on purpose: it is what the day is PAID, and it exists
on every shift, including shifts that never earn a minute of overtime. Filed
under Overtime Detail it read as an overtime figure and showed an empty
overtime block on shifts with overtime switched off.

The overtime block hangs off native `overtime_type`, the same field hrms uses
to show or hide its own Overtime section, so the two appear and disappear
together. attendance._compute_overtime stamps that field from the SHIFT rather
than from the outcome of the day, and clears it when the shift does not allow
overtime — without that the section flickered between days on one employee.

Idempotent — safe to re-run on every migrate.
"""

import json
from datetime import timedelta

import frappe
from frappe.utils import now_datetime

NATIVE_TAIL = "standard_working_hours"

# Native field hrms itself keys the Overtime section on. Reused so our block and
# hrms's appear and disappear as one.
OT_GATE = "eval:doc.overtime_type"

# (fieldname, insert_after, depends_on)
FIELD_ORDER = [
	("custom_att_hours_section", NATIVE_TAIL, None),
	("custom_base_hours", "custom_att_hours_section", None),
	("custom_att_ot_section", "custom_base_hours", OT_GATE),
	("custom_total_ot_hours", "custom_att_ot_section", OT_GATE),
	("custom_att_ot_col1", "custom_total_ot_hours", OT_GATE),
	("custom_early_ot_minutes", "custom_att_ot_col1", OT_GATE),
	("custom_late_ot_minutes", "custom_early_ot_minutes", OT_GATE),
	("custom_att_ot_col2", "custom_late_ot_minutes", OT_GATE),
	("custom_ot_status", "custom_att_ot_col2", OT_GATE),
]


# Native working_hours is a Float with no precision set, so it saves at the
# system default of 2 while custom_base_hours — derived from the very same
# measurement — is written at 4. A day reads 7.96 worked against 7.9592 payable
# and looks inconsistent on every report that shows both. 4 dp on both makes the
# two agree; it also stops a hundredth of an hour being lost on each row before
# the Salary Slip sums them.
def _set_working_hours_precision():
	if not frappe.db.exists("DocType", "Attendance"):
		return

	name = frappe.db.exists(
		"Property Setter", {"doc_type": "Attendance", "field_name": "working_hours", "property": "precision"}
	)
	if name:
		frappe.db.set_value("Property Setter", name, "value", "4", update_modified=False)
		return

	frappe.get_doc(
		{
			"doctype": "Property Setter",
			"doctype_or_field": "DocField",
			"doc_type": "Attendance",
			"field_name": "working_hours",
			"property": "precision",
			"property_type": "Select",
			"value": "4",
		}
	).insert(ignore_permissions=True)


def execute():
	if not frappe.db.exists("DocType", "Attendance"):
		return

	native_max_idx = (
		frappe.db.sql("SELECT MAX(idx) FROM `tabDocField` WHERE parent = 'Attendance'")[0][0] or 0
	)
	base_time = now_datetime()

	for i, (fieldname, insert_after, depends_on) in enumerate(FIELD_ORDER):
		name = f"Attendance-{fieldname}"
		if not frappe.db.exists("Custom Field", name):
			continue

		stamp = base_time + timedelta(seconds=i)
		frappe.db.sql(
			"""
			UPDATE `tabCustom Field`
			SET insert_after = %s, idx = %s, depends_on = %s, creation = %s, modified = %s
			WHERE name = %s
			""",
			(insert_after, native_max_idx + i + 1, depends_on, stamp, stamp, name),
		)

	_set_working_hours_precision()
	_reorder_field_order_property_setter()

	frappe.db.commit()
	frappe.clear_cache(doctype="Attendance")


def _reorder_field_order_property_setter():
	"""Move our block inside the `field_order` Property Setter, if one exists.

	Where Customize Form has written one it OVERRIDES insert_after for every
	field listed, while fields missing from the list still fall back to
	insert_after — which splits the block across the form. Attendance has one.
	"""
	name = frappe.db.exists("Property Setter", {"doc_type": "Attendance", "property": "field_order"})
	if not name:
		return

	try:
		order = json.loads(frappe.db.get_value("Property Setter", name, "value") or "[]")
	except (ValueError, TypeError):
		return
	if not order:
		return

	ours = [
		fieldname
		for fieldname, _, _ in FIELD_ORDER
		if frappe.db.exists("Custom Field", f"Attendance-{fieldname}")
	]
	remaining = [f for f in order if f not in ours]
	at = remaining.index(NATIVE_TAIL) + 1 if NATIVE_TAIL in remaining else len(remaining)

	rebuilt = remaining[:at] + ours + remaining[at:]
	if rebuilt != order:
		frappe.db.set_value("Property Setter", name, "value", json.dumps(rebuilt), update_modified=False)
