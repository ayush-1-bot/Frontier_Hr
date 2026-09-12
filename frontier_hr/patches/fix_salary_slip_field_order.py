"""Pin the Salary Slip custom-field layout so every site renders it the same.

Same role as fix_overtime_type_field_order.py plays for Overtime Type. Custom
Fields are ordered by `insert_after` and `idx`, and both drift: fields added at
different times land wherever they were inserted, and idx values collide when
two fields share an anchor. Observed on recode.com before this patch —
custom_payroll_basis and custom_regular_working_hours both at idx 26, and
custom_total_working_hours colliding with a neighbour at idx 24 — which
scattered the hours fields through the form.

Layout produced (below the last native field of the Details tab):

    Overtime                       [Section]
        Actual OT Hours            | Standard Multiplier
        Payable OT Hours           |

    Payroll Basis & Hours          [Section]
        Payroll Basis              | Regular Working Hours
                                   | Holiday Hours
                                   | Total Working Hours
                                   | Standard Day Hours
                                   | Standard Month Hours

The hours column carries depends_on custom_payroll_basis == "Working Hours", so
a Present / Absent slip shows the basis alone and none of the hours fields.

Idempotent — safe to re-run on every migrate.
"""

import json
from datetime import timedelta

import frappe
from frappe.utils import now_datetime

# Anchored on the LAST native field of the Details tab, never inside a native
# section. end_date sits in the middle of a three-column native section
# (end_date | salary_structure... | salary_slip_based_on_timesheet), so a
# Section Break placed after it split the native fields across our columns.
# Everything custom now starts cleanly below the native layout.
NATIVE_TAIL = "deduct_tax_for_unsubmitted_tax_exemption_proof"

FIELD_ORDER = [
	# === Overtime ===
	("custom_ot_section", NATIVE_TAIL),
	("custom_actual_ot_hours", "custom_ot_section"),
	("custom_payable_ot_hours", "custom_actual_ot_hours"),
	("custom_ot_col_break", "custom_payable_ot_hours"),
	("custom_standard_multiplier", "custom_ot_col_break"),
	# === Payroll basis & hours ===
	("custom_hours_section", "custom_standard_multiplier"),
	("custom_payroll_basis", "custom_hours_section"),
	("custom_hours_col_break", "custom_payroll_basis"),
	("custom_regular_working_hours", "custom_hours_col_break"),
	("custom_holiday_hours", "custom_regular_working_hours"),
	("custom_total_working_hours", "custom_holiday_hours"),
	("custom_standard_day_hours", "custom_total_working_hours"),
	("custom_standard_month_hours", "custom_standard_day_hours"),
	# The Sunday-deduction and WhatsApp blocks that company_hr_ext pins here are
	# deliberately absent: frontier_hr owns neither set of fields, and listing
	# them would write dead fieldnames into the field_order Property Setter.
]

HOURS_DEPENDS_ON = 'eval:doc.custom_payroll_basis=="Working Hours"'
HIDDEN_WHEN_NOT_HOURLY = (
	"custom_regular_working_hours",
	"custom_holiday_hours",
	"custom_total_working_hours",
	"custom_standard_day_hours",
	"custom_standard_month_hours",
)


# Hour fields are stored rounded to 2 dp by
# salary_slip.fetch_working_hours; without an explicit precision the form falls
# back to the system float precision and renders digits the value does not
# actually carry (84.6500000001). Display only — the stored figure is unchanged.
TWO_DP_FIELDS = (
	"custom_total_working_hours",
	"custom_regular_working_hours",
	"custom_holiday_hours",
	"custom_standard_day_hours",
	"custom_standard_month_hours",
	"custom_actual_ot_hours",
	"custom_payable_ot_hours",
)


def _set_two_decimal_precision():
	for fieldname in TWO_DP_FIELDS:
		name = f"Salary Slip-{fieldname}"
		if frappe.db.exists("Custom Field", name):
			frappe.db.set_value("Custom Field", name, "precision", "2", update_modified=False)


def execute():
	if not frappe.db.exists("DocType", "Salary Slip"):
		return

	native_max_idx = (
		frappe.db.sql("SELECT MAX(idx) FROM `tabDocField` WHERE parent = 'Salary Slip'")[0][0] or 0
	)
	base_time = now_datetime()

	for i, (fieldname, insert_after) in enumerate(FIELD_ORDER):
		name = f"Salary Slip-{fieldname}"
		if not frappe.db.exists("Custom Field", name):
			continue

		stamp = base_time + timedelta(seconds=i)
		# Raw SQL: ordering depends on creation as a tie-breaker, and going
		# through the ORM would overwrite it.
		frappe.db.sql(
			"""
			UPDATE `tabCustom Field`
			SET insert_after = %s, idx = %s, creation = %s, modified = %s
			WHERE name = %s
			""",
			(insert_after, native_max_idx + i + 1, stamp, stamp, name),
		)

	_set_two_decimal_precision()
	_reorder_field_order_property_setter()

	# The hours fields only make sense on a Working Hours shift. Re-asserted
	# here rather than trusted from the fixture so a site that installed an
	# earlier version still ends up consistent.
	for fieldname in HIDDEN_WHEN_NOT_HOURLY:
		name = f"Salary Slip-{fieldname}"
		if frappe.db.exists("Custom Field", name):
			frappe.db.set_value("Custom Field", name, "depends_on", HOURS_DEPENDS_ON, update_modified=False)

	frappe.db.commit()
	frappe.clear_cache(doctype="Salary Slip")


def _reorder_field_order_property_setter():
	"""Move our block inside the `field_order` Property Setter, if one exists.

	Opening Customize Form on a DocType writes a Property Setter that hard-codes
	the entire field order. Where it exists it OVERRIDES `insert_after`
	completely — so setting insert_after alone silently does nothing for any
	field already listed there, while fields missing from the list fall back to
	insert_after and end up somewhere else entirely. That split is what
	scattered the block on recode.com: custom_actual_ot_hours and
	custom_standard_multiplier sat pinned after end_date by the saved order,
	mid-way through a native three-column section, while the new section breaks
	placed themselves correctly and were rendered apart from them.

	Fresh sites have no such Property Setter and need nothing here — plain
	insert_after already produces the right layout.
	"""
	name = frappe.db.exists("Property Setter", {"doc_type": "Salary Slip", "property": "field_order"})
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
		if frappe.db.exists("Custom Field", f"Salary Slip-{fieldname}")
	]
	remaining = [f for f in order if f not in ours]

	if NATIVE_TAIL in remaining:
		at = remaining.index(NATIVE_TAIL) + 1
	else:
		at = len(remaining)

	rebuilt = remaining[:at] + ours + remaining[at:]
	if rebuilt == order:
		return

	frappe.db.set_value("Property Setter", name, "value", json.dumps(rebuilt), update_modified=False)
