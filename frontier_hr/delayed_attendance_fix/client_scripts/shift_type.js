// Additive to hrms core's shift_type.js — does not replace it.
// Adds a button that finds Attendance records marked from a single
// Employee Checkin while a second (late-synced) checkin now exists
// unlinked, lets HR Manager review them, then cancels + reprocesses.

frappe.ui.form.on("Shift Type", {
	refresh: function (frm) {
		if (frm.doc.__islocal) return;
		if (!frappe.user.has_role("HR Manager")) return;

		frm.add_custom_button(__("Fix Delayed Sync Attendance"), () => {
			if (!frm.doc.enable_auto_attendance) {
				frm.scroll_to_field("enable_auto_attendance");
				frappe.throw(__("Please Enable Auto Attendance and complete the setup first."));
			}
			frontier_hr.delayed_attendance_fix.show_dialog(frm);
		});
	},
});

frappe.provide("frontier_hr.delayed_attendance_fix");

frontier_hr.delayed_attendance_fix.show_dialog = function (frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Fix Delayed Sync Attendance — {0}", [frm.doc.name]),
		size: "extra-large",
		fields: [
			{
				fieldtype: "Date",
				fieldname: "from_date",
				label: __("From Date"),
				default: frappe.datetime.add_days(frappe.datetime.get_today(), -30),
				reqd: 1,
			},
			{
				fieldtype: "Date",
				fieldname: "to_date",
				label: __("To Date"),
				default: frappe.datetime.get_today(),
				reqd: 1,
			},
			{ fieldtype: "Column Break" },
			{
				fieldtype: "Link",
				fieldname: "employee",
				label: __("Employee"),
				options: "Employee",
			},
			{
				fieldtype: "Link",
				fieldname: "department",
				label: __("Department"),
				options: "Department",
			},
			{ fieldtype: "Column Break" },
			{
				fieldtype: "Button",
				fieldname: "scan",
				label: __("Scan"),
				click: () => scan(),
			},
			{ fieldtype: "Section Break" },
			{
				fieldtype: "HTML",
				fieldname: "results",
			},
		],
		primary_action_label: __("Fix Selected"),
		primary_action: () => fix_selected(),
	});

	dialog.disable_primary_action();
	dialog.set_value("results", `<div class="text-muted">${__("Click Scan to find broken records.")}</div>`);
	dialog.show();

	let last_rows = [];

	function scan() {
		const from_date = dialog.get_value("from_date");
		const to_date = dialog.get_value("to_date");
		const employee = dialog.get_value("employee");
		const department = dialog.get_value("department");

		if (!from_date || !to_date) {
			frappe.throw(__("Please set both From Date and To Date."));
		}
		if (to_date < from_date) {
			frappe.throw(__("To Date cannot be before From Date."));
		}

		dialog.set_value("results", `<div class="text-muted">${__("Scanning...")}</div>`);
		dialog.disable_primary_action();

		frappe.call({
			method: "frontier_hr.delayed_attendance_fix.api.get_broken_attendance_records",
			args: { shift: frm.doc.name, from_date, to_date, employee, department },
			freeze: true,
			callback: (r) => {
				last_rows = r.message || [];
				render_results(last_rows);
			},
		});
	}

	function render_results(rows) {
		if (!rows.length) {
			dialog.set_value(
				"results",
				`<div class="text-muted">${__("No broken records found in this range.")}</div>`
			);
			dialog.disable_primary_action();
			return;
		}

		const fixable_count = rows.filter((d) => !d.payroll_locked).length;
		const blocked_count = rows.length - fixable_count;

		const statuses = Array.from(new Set(rows.map((d) => d.status))).sort();
		const status_options =
			`<option value="">${__("All Statuses")}</option>` +
			statuses
				.map((s) => `<option value="${frappe.utils.escape_html(s)}">${frappe.utils.escape_html(s)}</option>`)
				.join("");

		const rows_html = rows
			.map((d) => {
				const locked = d.payroll_locked;
				return `
					<tr class="${locked ? "text-muted" : ""}" data-status="${frappe.utils.escape_html(d.status)}">
						<td>
							<input type="checkbox" class="row-check" data-attendance="${frappe.utils.escape_html(d.attendance)}"
								${locked ? "disabled" : "checked"}>
						</td>
						<td>${frappe.utils.escape_html(d.employee_name || d.employee)}</td>
						<td>${frappe.datetime.str_to_user(d.attendance_date)}</td>
						<td>${frappe.utils.escape_html(d.status)}</td>
						<td>${locked ? __("Payroll locked") : ""}</td>
					</tr>`;
			})
			.join("");

		dialog.fields_dict.results.$wrapper.html(`
			<div class="margin-bottom">
				${__("{0} record(s) will be corrected. {1} blocked (payroll locked).", [
					fixable_count,
					blocked_count,
				])}
			</div>
			<div class="margin-bottom" style="display:flex; gap:10px; align-items:center;">
				<label style="margin:0;">${__("Filter by Status")}:</label>
				<select class="form-control status-filter" style="width:auto;">${status_options}</select>
				<span class="text-muted visible-count"></span>
			</div>
			<table class="table table-bordered">
				<thead>
					<tr>
						<th><input type="checkbox" class="select-all" checked></th>
						<th>${__("Employee")}</th>
						<th>${__("Date")}</th>
						<th>${__("Status")}</th>
						<th></th>
					</tr>
				</thead>
				<tbody>${rows_html}</tbody>
			</table>
		`);

		const $wrapper = dialog.fields_dict.results.$wrapper;

		function update_visible_count() {
			const visible = $wrapper.find("tbody tr:visible").length;
			const checked = $wrapper.find("tbody tr:visible input.row-check:checked").length;
			$wrapper.find(".visible-count").text(__("{0} visible, {1} selected", [visible, checked]));
		}

		function sync_select_all() {
			const $visible = $wrapper.find("tbody tr:visible input.row-check:not(:disabled)");
			const all_checked = $visible.length > 0 && $visible.filter(":checked").length === $visible.length;
			$wrapper.find(".select-all").prop("checked", all_checked);
		}

		$wrapper.find(".status-filter").on("change", function () {
			const val = $(this).val();
			$wrapper.find("tbody tr").each(function () {
				const match = !val || $(this).attr("data-status") === val;
				$(this).toggle(match);
			});
			sync_select_all();
			update_visible_count();
		});

		$wrapper.find(".select-all").on("change", function () {
			const checked = $(this).prop("checked");
			$wrapper.find("tbody tr:visible input.row-check:not(:disabled)").prop("checked", checked);
			update_visible_count();
		});

		$wrapper.on("change", "input.row-check", function () {
			sync_select_all();
			update_visible_count();
		});

		update_visible_count();

		if (fixable_count) {
			dialog.enable_primary_action();
		} else {
			dialog.disable_primary_action();
		}
	}

	function fix_selected() {
		const selected = dialog.fields_dict.results.$wrapper
			.find("tbody tr:visible input.row-check:checked")
			.map(function () {
				return $(this).attr("data-attendance");
			})
			.get();

		if (!selected.length) {
			frappe.throw(__("Select at least one record."));
		}

		frappe.confirm(
			__("Cancel and reprocess {0} attendance record(s)? This cannot be undone.", [selected.length]),
			() => {
				frappe.call({
					method: "frontier_hr.delayed_attendance_fix.api.fix_delayed_sync_attendance",
					args: { shift: frm.doc.name, attendance_names: selected },
					freeze: true,
					callback: (r) => {
						const msg = r.message || {};
						frappe.msgprint(
							__("Cancelled {0} record(s). {1}", [
								msg.cancelled || 0,
								msg.reprocess_message || "",
							])
						);
						dialog.hide();
						frm.reload_doc();
					},
				});
			}
		);
	}
};
