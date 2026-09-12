"""Pin the Overtime Type custom-field layout so every site renders it the same.

Companion to fix_shift_type_field_order, fix_attendance_field_order and
fix_salary_slip_field_order. Custom Fields are ordered by `insert_after` and
`idx`, and both drift as fields are added over time: fields land wherever they
were inserted, and idx values collide when two share an anchor.

Layout produced, anchored on `weekend_multiplier` (the last native field, so
nothing native is split):

    OT Buffer Rules                [custom section]
        Pre Shift Buffer (Minutes) | Post Shift Buffer (Minutes)

    OT Limits                      [custom section]
        Weekly Max OT Hours        | Quarterly Max OT Hours

    OT Alerts                      [custom section]
        Alert Threshold Percent
        Alert Recipients (table)

Idempotent — safe to re-run on every migrate.
"""

import json
from datetime import timedelta

import frappe
from frappe.utils import now_datetime

NATIVE_TAIL = "weekend_multiplier"

# (fieldname, insert_after)
FIELD_ORDER = [
	# === OT Buffer Rules ===
	("ot_buffer_section", NATIVE_TAIL),
	("pre_shift_buffer_minutes", "ot_buffer_section"),
	("ot_buffer_col_break", "pre_shift_buffer_minutes"),
	("post_shift_buffer_minutes", "ot_buffer_col_break"),
	# === OT Limits ===
	("ot_limits_section", "post_shift_buffer_minutes"),
	("weekly_max_ot_hours", "ot_limits_section"),
	("ot_limits_col_break", "weekly_max_ot_hours"),
	("quarterly_max_ot_hours", "ot_limits_col_break"),
	# === Alerts ===
	("ot_alerts_section", "quarterly_max_ot_hours"),
	("alert_threshold_percent", "ot_alerts_section"),
	("alert_recipients", "alert_threshold_percent"),
]


def execute():
	if not frappe.db.exists("DocType", "Overtime Type"):
		return

	native_max_idx = (
		frappe.db.sql("SELECT MAX(idx) FROM `tabDocField` WHERE parent = 'Overtime Type'")[0][0] or 0
	)
	base_time = now_datetime()

	for i, (fieldname, insert_after) in enumerate(FIELD_ORDER):
		name = f"Overtime Type-{fieldname}"
		if not frappe.db.exists("Custom Field", name):
			continue

		stamp = base_time + timedelta(seconds=i)
		# Raw SQL: ordering uses creation as a tie-breaker and the ORM would
		# overwrite it.
		frappe.db.sql(
			"""
			UPDATE `tabCustom Field`
			SET insert_after = %s, idx = %s, creation = %s, modified = %s
			WHERE name = %s
			""",
			(insert_after, native_max_idx + i + 1, stamp, stamp, name),
		)

	_reorder_field_order_property_setter()

	frappe.db.commit()
	frappe.clear_cache(doctype="Overtime Type")


def _reorder_field_order_property_setter():
	"""Move our block inside the `field_order` Property Setter, if one exists.

	Opening Customize Form writes a Property Setter that hard-codes the whole
	field order and OVERRIDES insert_after for every field listed in it, while
	fields missing from it still fall back to insert_after — which splits the
	block in two.
	"""
	name = frappe.db.exists("Property Setter", {"doc_type": "Overtime Type", "property": "field_order"})
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
		for fieldname, _ in FIELD_ORDER
		if frappe.db.exists("Custom Field", f"Overtime Type-{fieldname}")
	]
	remaining = [f for f in order if f not in ours]
	at = remaining.index(NATIVE_TAIL) + 1 if NATIVE_TAIL in remaining else len(remaining)

	rebuilt = remaining[:at] + ours + remaining[at:]
	if rebuilt != order:
		frappe.db.set_value("Property Setter", name, "value", json.dumps(rebuilt), update_modified=False)
