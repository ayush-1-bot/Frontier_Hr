app_name = "frontier_hr"
app_title = "Frontier Hr"
app_publisher = "dev@frontiersoftech.com"
app_description = "HR For Frontier Softech"
app_email = "dev@frontiersoftech.com"
app_license = "mit"

# Overtime Extension drives Attendance, Overtime Slip and Salary Slip, all of
# which are hrms doctypes.
required_apps = ["frappe", "hrms"]

# ---------------------------------------------------------------------------
# Document Events
# ---------------------------------------------------------------------------
doc_events = {
	"Salary Slip": {
		# before_validate, NOT before_save: Frappe runs before_validate ->
		# validate -> before_save, and SalarySlip.validate() calls
		# calculate_net_pay(). Anything written in before_save lands AFTER the
		# salary component formulas have been evaluated, so formulas would read
		# the previous save's values. Fires on both the save and the submit
		# path, so no before_submit duplicate is needed.
		"before_validate": [
			"frontier_hr.overtime_extension.overrides.salary_slip.fetch_ot_hours",
			"frontier_hr.overtime_extension.overrides.salary_slip.fetch_working_hours",
		],
	},
	"Attendance": {
		"before_save": "frontier_hr.overtime_extension.overrides.attendance.apply_buffer_logic",
		"before_submit": "frontier_hr.overtime_extension.overrides.attendance.apply_buffer_logic",
	},
	# hrms flips the Attendance day to On Leave with db_set, which runs no
	# hooks — so the hours have to be recomputed from here. See the module
	# docstring in overtime_extension/overrides/leave_application.py.
	"Leave Application": {
		"on_submit": "frontier_hr.overtime_extension.overrides.leave_application.recompute_attendance_hours",
		"on_cancel": "frontier_hr.overtime_extension.overrides.leave_application.recompute_attendance_hours",
		"on_update_after_submit": "frontier_hr.overtime_extension.overrides.leave_application.recompute_attendance_hours",
	},
	# Lunch window -> custom_lunch_break_minutes. See overrides/shift_type.py.
	"Shift Type": {
		"validate": "frontier_hr.overtime_extension.overrides.shift_type.sync_lunch_minutes",
	},
	"Overtime Slip": {
		"validate": "frontier_hr.overtime_extension.overrides.overtime_slip.validate_ot_slip",
		"on_submit": "frontier_hr.overtime_extension.overrides.overtime_slip.on_submit_ot_slip",
	},
}

# ---------------------------------------------------------------------------
# Scheduled Tasks
# ---------------------------------------------------------------------------
scheduler_events = {
	# Self-gates on Miss Punch Report Settings (enabled + send_time +
	# last_run_date), so running it every hour costs one Singles read.
	"hourly": [
		"frontier_hr.overtime_extension.tasks.run_if_due",
	],
	# Branch-scoped replacement for hrms send_birthday_reminders (stopped in
	# after_migrate below). See birthday_reminder/tasks.py.
	"daily": [
		"frontier_hr.birthday_reminder.tasks.send_branch_birthday_reminders",
	],
	"cron": {
		# Daily 11 PM — daily threshold alert, after shifts are closed.
		"0 23 * * *": [
			"frontier_hr.overtime_extension.tasks.daily_ot_threshold_check",
		],
		# Every Monday 9 AM — weekly threshold alert.
		"0 9 * * 1": [
			"frontier_hr.overtime_extension.tasks.weekly_ot_threshold_check",
		],
		# 1st of every month at 9 AM — the task self-gates on the company's
		# fiscal-quarter boundary (Apr-Mar by default; reads Frappe FY setting).
		"0 9 1 * *": [
			"frontier_hr.overtime_extension.tasks.quarterly_ot_threshold_check",
		],
	},
}

# ---------------------------------------------------------------------------
# After Migrate
# ---------------------------------------------------------------------------
# These pin the Custom Field layout (insert_after, idx, creation tie-breaker,
# depends_on, precision) on the four doctypes this app extends. They run on
# after_migrate rather than from patches.txt on purpose: fixture import happens
# during every migrate and resets those columns, and a patches.txt entry would
# run once and then never again. All four are idempotent.
after_migrate = [
	"frontier_hr.patches.fix_overtime_type_field_order.execute",
	"frontier_hr.patches.fix_shift_type_field_order.execute",
	"frontier_hr.patches.fix_attendance_field_order.execute",
	"frontier_hr.patches.fix_salary_slip_field_order.execute",
	"frontier_hr.birthday_reminder.tasks.stop_hrms_birthday_job",
]

# ---------------------------------------------------------------------------
# Client Scripts
# ---------------------------------------------------------------------------
doctype_js = {
	"Overtime Slip": "overtime_extension/client_scripts/overtime_slip.js",
	# Additive to hrms core's shift_type.js — adds the "Fix Delayed Sync
	# Attendance" button. Server side is whitelisted methods only, no hooks.
	"Shift Type": "delayed_attendance_fix/client_scripts/shift_type.js",
}

# ---------------------------------------------------------------------------
# Class Overrides
# ---------------------------------------------------------------------------
override_doctype_class = {
	"Overtime Slip": "frontier_hr.overtime_extension.overrides.overtime_slip_class.CustomOvertimeSlip",
	# Populates the hours/basis fields on the pre-save preview path, where
	# before_validate never runs — see the salary_slip_class docstring.
	"Salary Slip": "frontier_hr.overtime_extension.overrides.salary_slip_class.CustomSalarySlip",
	"Salary Structure Assignment": "frontier_hr.overtime_extension.overrides.salary_structure_assignment.CustomSalaryStructureAssignment",
}

# ---------------------------------------------------------------------------
# Fixtures
# Custom Fields exported so they install automatically on bench migrate.
# The OT Slip Batch / Miss Punch / OT Alert Recipient doctypes ship in-app
# under overtime_extension/doctype and need no fixture.
# ---------------------------------------------------------------------------
fixtures = [
	{
		"doctype": "Custom Field",
		"filters": [
			["dt", "in", ["Overtime Type", "Shift Type", "Attendance", "Overtime Slip", "Salary Slip", "HR Settings"]],
			[
				"fieldname",
				"in",
				[
					# Overtime Type — buffers, caps, alert recipients
					"ot_buffer_section",
					"pre_shift_buffer_minutes",
					"ot_buffer_col_break",
					"post_shift_buffer_minutes",
					"ot_limits_section",
					"weekly_max_ot_hours",
					"ot_limits_col_break",
					"quarterly_max_ot_hours",
					"ot_alerts_section",
					"alert_threshold_percent",
					"alert_recipients",
					# Shift Type — OT rules and payroll basis
					"custom_ot_rules_section",
					"custom_lunch_break_minutes",
					"custom_lunch_start",
					"custom_lunch_end",
					"custom_ot_rules_col_break",
					"custom_ot_qualifying_hours",
					"custom_apply_ot_buffers",
					"custom_cap_base_at_shift_hours",
					"custom_payroll_section",
					"custom_payroll_basis",
					"custom_payroll_col_break",
					"custom_pay_hours_when_absent",
					# Attendance — computed OT and base hours
					"custom_att_ot_section",
					"custom_att_ot_col1",
					"custom_early_ot_minutes",
					"custom_att_ot_col2",
					"custom_late_ot_minutes",
					"custom_total_ot_hours",
					"custom_ot_status",
					"custom_att_hours_section",
					"custom_base_hours",
					# Overtime Slip — actual vs payable after caps
					"custom_actual_ot_hours",
					"custom_payable_ot_hours",
					# Salary Slip — OT and hours-basis inputs to the formulas
					"custom_ot_section",
					"custom_ot_col_break",
					"custom_actual_ot_hours",
					"custom_payable_ot_hours",
					"custom_standard_multiplier",
					"custom_hours_section",
					"custom_hours_col_break",
					"custom_total_working_hours",
					"custom_regular_working_hours",
					"custom_holiday_hours",
					"custom_standard_day_hours",
					"custom_standard_month_hours",
					"custom_payroll_basis",
					# HR Settings — branch-wise birthday toggle (birthday_reminder/)
					"custom_branch_wise_birthday_reminders",
				],
			],
		],
	},
]
