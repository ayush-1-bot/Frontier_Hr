"""Shift scoping shared by both attendance policies.

`shift_type` used to be a single Link on each policy, so a site that ran the
same rule on three shifts needed three identical policies. `shift_types`
replaces it with a Table MultiSelect; an empty table still means every shift.

The old Link is kept, hidden, and folded into the table on the next save, so no
existing policy quietly stops matching the shift it was set up for.
"""

import frappe

SHIFT_CHILD = "Attendance Policy Shift"
SHIFT_FIELD = "shift_types"


def pinned_shifts(doctype, names):
	"""{policy: {shift, ...}} for the policies that pin shifts.

	One query for the whole candidate set rather than loading each policy: the
	resolver runs on every Attendance save.
	"""
	if not names:
		return {}

	rows = frappe.get_all(
		SHIFT_CHILD,
		filters={"parenttype": doctype, "parentfield": SHIFT_FIELD, "parent": ["in", list(names)]},
		fields=["parent", "shift_type"],
	)

	pinned = {}
	for row in rows:
		pinned.setdefault(row.parent, set()).add(row.shift_type)
	return pinned


def fold_legacy_shift(policy):
	"""Move a policy's old single `shift_type` into `shift_types`, once.

	Called from validate on both policies. Idempotent: the Link is cleared, so
	a second save finds nothing to move.
	"""
	if not policy.get("shift_type"):
		return

	if policy.shift_type not in [row.shift_type for row in policy.get(SHIFT_FIELD) or []]:
		policy.append(SHIFT_FIELD, {"shift_type": policy.shift_type})
	policy.shift_type = None


def dedupe_shifts(policy):
	"""Drop repeated shifts, which would only double a policy's specificity."""
	seen, kept = set(), []
	for row in policy.get(SHIFT_FIELD) or []:
		if row.shift_type in seen:
			continue
		seen.add(row.shift_type)
		kept.append(row)
	policy.set(SHIFT_FIELD, kept)
