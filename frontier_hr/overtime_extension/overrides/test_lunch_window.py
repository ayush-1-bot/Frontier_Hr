"""Self-check for the Shift Type lunch window (attendance._day_lunch_hours).
Run: bench --site <site> execute frontier_hr.overtime_extension.overrides.test_lunch_window.run
"""

import frappe

from frontier_hr.overtime_extension.overrides.attendance import _effective_from_punches


def _hours(in_t, out_t, shift, date="2026-09-01", out_date=None):
	doc = frappe._dict(attendance_date=date, in_time=f"{date} {in_t}", out_time=f"{out_date or date} {out_t}")
	return round(_effective_from_punches(doc, shift), 2)


def run():
	day = frappe._dict(start_time="09:00:00", custom_lunch_break_minutes=30,
		custom_lunch_start="13:00:00", custom_lunch_end="13:30:00")
	assert _hours("09:00:00", "18:00:00", day) == 8.5  # covers lunch: 30 min off
	assert _hours("13:30:00", "18:00:00", day) == 4.5  # came after lunch: nothing off
	assert _hours("09:00:00", "13:00:00", day) == 4.0  # left before lunch: nothing off
	assert _hours("13:15:00", "18:00:00", day) == 4.5  # partial: 15 min off
	assert _hours("09:00:00", "13:29:00", day) <= _hours("09:00:00", "13:30:00", day)  # longer never pays less

	flat = frappe._dict(start_time="09:00:00", custom_lunch_break_minutes=30)
	assert _hours("13:30:00", "18:00:00", flat) == 4.0  # no window: old flat deduction

	night = frappe._dict(start_time="22:00:00", custom_lunch_break_minutes=30,
		custom_lunch_start="02:00:00", custom_lunch_end="02:30:00")
	assert _hours("22:00:00", "06:00:00", night, out_date="2026-09-02") == 7.5  # lunch next day
	assert _hours("22:00:00", "01:00:00", night, out_date="2026-09-02") == 3.0  # left before lunch

	print("lunch window: all checks passed")
