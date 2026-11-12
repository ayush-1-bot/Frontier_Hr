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


# Roles allowed to see and change the accrual policy link on the Employee, and
# the roles allowed to see it without changing it. The field sits at permlevel 1
# so HR User -- who has full write at level 0 -- cannot reassign a policy.
ACCRUAL_PERMLEVEL = 1
ACCRUAL_WRITE_ROLES = ("HR Manager",)
ACCRUAL_READ_ROLES = ("HR User",)


def ensure_accrual_permlevel_perms():
	"""Grant level-1 perms on Employee so the policy field stays visible to HR.

	A field at a permlevel the user has no read perm on is hidden outright, not
	just locked -- so the level needs its own rows or the field vanishes for
	everyone who should still see it.

	Goes through frappe.permissions rather than inserting Custom DocPerm
	directly: the first Custom DocPerm row on a doctype makes Frappe ignore ALL
	of its standard DocPerms, so a hand-inserted row would silently revoke
	every level-0 permission on Employee. add_permission copies the standard
	rows across first.
	"""
	from frappe.permissions import add_permission, update_permission_property

	for role in ACCRUAL_WRITE_ROLES + ACCRUAL_READ_ROLES:
		if not frappe.db.exists("Role", role):
			continue
		if not frappe.db.exists(
			"Custom DocPerm",
			{"parent": "Employee", "role": role, "permlevel": ACCRUAL_PERMLEVEL},
		):
			add_permission("Employee", role, ACCRUAL_PERMLEVEL, "read")

		update_permission_property(
			"Employee", role, ACCRUAL_PERMLEVEL, "write",
			1 if role in ACCRUAL_WRITE_ROLES else 0,
		)

	frappe.clear_cache(doctype="Employee")
