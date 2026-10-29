# Copyright (c) 2026, Frontier Softech
# For license information, please see license.txt

import frappe


def ensure_module_defs():
	"""Create a Module Def for every module in modules.txt.

	Runs on after_migrate because add_module_defs only runs when an app is
	INSTALLED. An app that gains a module later has the line in modules.txt but
	no Module Def on sites installed before it, and `DocType.module` is a Link
	to Module Def -- so every doctype in the new module fails to install while
	its custom fields, which come from fixtures, appear as normal. The result
	looks like "the fields are there but the doctypes are not".
	"""
	from frappe.installer import add_module_defs

	add_module_defs("frontier_hr", ignore_if_duplicate=True)
	frappe.clear_cache()
