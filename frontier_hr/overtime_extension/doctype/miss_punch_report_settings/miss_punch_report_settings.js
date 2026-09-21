// Recipient rows store their branches as a comma-separated list (a child row
// cannot hold a Table MultiSelect). This button edits that list with checkboxes.
frappe.ui.form.on("Miss Punch Recipient", {
	async select_branches(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		const selected = (row.branch || "").split(",").map((b) => b.trim()).filter(Boolean);
		const branches = await frappe.db.get_list("Branch", { pluck: "name", limit: 0, order_by: "name asc" });

		const dialog = new frappe.ui.Dialog({
			title: __("Branches for {0}", [row.full_name || row.user || __("recipient")]),
			fields: [
				{
					fieldname: "branches",
					fieldtype: "MultiCheck",
					label: __("Leave all unticked for all branches"),
					columns: 2,
					options: branches.map((b) => ({ label: b, value: b, checked: selected.includes(b) })),
				},
			],
			primary_action_label: __("Set"),
			primary_action({ branches }) {
				frappe.model.set_value(cdt, cdn, "branch", (branches || []).join(", "));
				dialog.hide();
			},
		});
		dialog.show();
	},
});
