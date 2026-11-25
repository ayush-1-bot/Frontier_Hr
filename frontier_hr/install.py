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


# Policies whose old single shift_type Link has to move into shift_types.
SHIFT_SCOPED_POLICIES = (
	"Attendance Leave Accrual Policy",
	"Late Coming and Early Out Deduction Policy",
)


def fold_policy_shift_types():
	"""Move every policy's old single shift_type into the Shift Types table.

	The controllers fold it on validate, but a policy nobody opens would keep a
	hidden Link that only the legacy branch of the resolver still reads. Done
	here so the switch to a multi-shift table needs no re-saving by hand.

	Idempotent: the fold clears the Link, so a second migrate finds nothing.
	"""
	from frontier_hr.policy_scope import fold_legacy_shift

	for doctype in SHIFT_SCOPED_POLICIES:
		if not frappe.db.exists("DocType", doctype):
			continue
		# A policy that has not been given the Shift Types table yet keeps its
		# Link, which the resolver still reads. Nothing to fold into.
		if not frappe.get_meta(doctype).has_field("shift_types"):
			continue
		for name in frappe.get_all(doctype, filters={"shift_type": ["is", "set"]}, pluck="name"):
			policy = frappe.get_doc(doctype, name)
			fold_legacy_shift(policy)
			# save(), not db_set: the table row has to be inserted as a child.
			policy.flags.ignore_permissions = True
			policy.save()


# Workspaces that must be visible to HR, by name.
APP_WORKSPACES = ("Leave Accrual", "Deduction Policy", "Short Leave")

# Roles allowed to see them in the sidebar.
WORKSPACE_ROLES = ("HR Manager", "HR User")


def sync_app_workspaces():
	"""Clear is_hidden and put the HR roles on this app's workspaces.

	A migrate does not push either back onto a Workspace that already exists,
	so a site that installed one while it was hidden, or before the roles were
	named, keeps the old state however the shipped JSON changes.

	A Workspace with roles is shown only to those roles. Anyone else keeps
	every link in it -- the doctype permissions are what grant access; this
	only decides whose sidebar it clutters.
	"""
	wanted = {role for role in WORKSPACE_ROLES if frappe.db.exists("Role", role)}

	for name in APP_WORKSPACES:
		if not frappe.db.exists("Workspace", name):
			continue

		doc = frappe.get_doc("Workspace", name)
		changed = False

		if doc.is_hidden:
			doc.is_hidden = 0
			changed = True

		if {row.role for row in doc.roles} != wanted:
			doc.set("roles", [{"role": role} for role in sorted(wanted)])
			changed = True

		if changed:
			# save(), not db_set: roles is a child table.
			doc.flags.ignore_permissions = True
			doc.save()

	frappe.clear_cache()
