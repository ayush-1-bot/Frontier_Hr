"""Disable the ESI Monthly Contribution and Employee EWL reports (2026-09-21).

Not needed, and both fail with "Unknown column 'custom_esi_no'" on sites without
that Employee field. Setting disabled=1 in the report .json is not enough:
frappe's import_file keeps the site's own `disabled` value for Reports, so it is
set here once. Report files stay on disk.
"""

import frappe

REPORTS = ("ESI Monthly Contribution Report", "Employee EWL Report")


def execute():
	for name in REPORTS:
		if frappe.db.exists("Report", name):
			frappe.db.set_value("Report", name, "disabled", 1)
