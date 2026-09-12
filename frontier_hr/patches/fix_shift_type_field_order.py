"""Pin the Shift Type custom-field layout so every site renders it the same.

Companion to fix_overtime_type_field_order (Overtime Type) and
fix_salary_slip_field_order (Salary Slip). Custom Fields are ordered by
`insert_after` and `idx`, and both drift as fields are added over time.
Observed on recode.com before this patch: custom_payroll_basis and
custom_apply_ot_buffers both sat at idx 29, and all five settings stacked into
a single column below the native Overtime section, each with a long
description — an unreadable wall of text.

Layout produced, anchored on `overtime_type` (the last native field, so nothing
native is split):

    Overtime                       [native section, untouched]
        Allow Overtime
        Overtime Type              [native, hidden unless Allow Overtime]

    Overtime Rules                 [custom section, hidden unless Allow Overtime]
        Minimum Hours Before OT    | Apply Shift Buffers

    Payroll & Hours                [custom section, ALWAYS visible]
        Payroll Basis              | Cap Base Hours at Shift Hours
        Lunch Break (Minutes)      |

The split matters. The lunch break and the base cap were originally filed
under Overtime Rules, which read as though they were overtime settings —
they are not. Both apply to a shift that never earns a minute of overtime:
the break comes off every hour figure the shift produces (Attendance working
hours, the credited day for leave/WFH/holidays, the Salary Slip divisor), and
the cap decides whether a long day is paid past a standard day. Only the two
genuine overtime settings sit behind Allow Overtime, matching what hrms
already does with `overtime_type`.

Idempotent — safe to re-run on every migrate.
"""

import json
from datetime import timedelta

import frappe
from frappe.utils import now_datetime

NATIVE_TAIL = "overtime_type"

# Gate for the two genuine overtime settings. Same expression hrms puts on its
# own `overtime_type`, so the whole overtime block appears and disappears as one.
OT_GATE = "eval:doc.allow_overtime"

# Paying an Absent day's hours only means anything where base pay is measured in
# hours at all. On Present / Absent basis the day is priced by payment_days and
# this app writes no hours, so the setting would do nothing and is hidden.
HOURS_BASIS_GATE = 'eval:doc.custom_payroll_basis == "Working Hours"' 

# (fieldname, insert_after, depends_on)
FIELD_ORDER = [
	("custom_ot_rules_section", NATIVE_TAIL, OT_GATE),
	("custom_ot_qualifying_hours", "custom_ot_rules_section", OT_GATE),
	("custom_ot_rules_col_break", "custom_ot_qualifying_hours", OT_GATE),
	("custom_apply_ot_buffers", "custom_ot_rules_col_break", OT_GATE),
	("custom_payroll_section", "custom_apply_ot_buffers", None),
	("custom_payroll_basis", "custom_payroll_section", None),
	("custom_lunch_break_minutes", "custom_payroll_basis", None),
	("custom_payroll_col_break", "custom_lunch_break_minutes", None),
	("custom_cap_base_at_shift_hours", "custom_payroll_col_break", None),
	("custom_pay_hours_when_absent", "custom_cap_base_at_shift_hours", HOURS_BASIS_GATE),
]


def execute():
	if not frappe.db.exists("DocType", "Shift Type"):
		return

	native_max_idx = (
		frappe.db.sql("SELECT MAX(idx) FROM `tabDocField` WHERE parent = 'Shift Type'")[0][0] or 0
	)
	base_time = now_datetime()

	for i, (fieldname, insert_after, depends_on) in enumerate(FIELD_ORDER):
		name = f"Shift Type-{fieldname}"
		if not frappe.db.exists("Custom Field", name):
			continue

		stamp = base_time + timedelta(seconds=i)
		# Raw SQL: ordering uses creation as a tie-breaker and the ORM would
		# overwrite it. depends_on rides along in the same statement — a site
		# installed before the fields were gated keeps showing the overtime
		# settings on every shift until it is set here, and fixtures alone do
		# not restate it on records that already exist.
		frappe.db.sql(
			"""
			UPDATE `tabCustom Field`
			SET insert_after = %s, idx = %s, depends_on = %s, creation = %s, modified = %s
			WHERE name = %s
			""",
			(insert_after, native_max_idx + i + 1, depends_on, stamp, stamp, name),
		)

	_reorder_field_order_property_setter()

	frappe.db.commit()
	frappe.clear_cache(doctype="Shift Type")


def _reorder_field_order_property_setter():
	"""Move our block inside the `field_order` Property Setter, if one exists.

	Opening Customize Form writes a Property Setter that hard-codes the whole
	field order and OVERRIDES insert_after for every field listed in it, while
	fields missing from it still fall back to insert_after — which splits the
	block in two. Shift Type has no such Property Setter today, but one appears
	the moment anyone opens Customize Form on it.
	"""
	name = frappe.db.exists("Property Setter", {"doc_type": "Shift Type", "property": "field_order"})
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
		if frappe.db.exists("Custom Field", f"Shift Type-{fieldname}")
	]
	remaining = [f for f in order if f not in ours]
	at = remaining.index(NATIVE_TAIL) + 1 if NATIVE_TAIL in remaining else len(remaining)

	rebuilt = remaining[:at] + ours + remaining[at:]
	if rebuilt != order:
		frappe.db.set_value("Property Setter", name, "value", json.dumps(rebuilt), update_modified=False)
