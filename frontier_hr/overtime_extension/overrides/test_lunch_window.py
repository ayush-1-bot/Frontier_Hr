"""Self-check for the Shift Type lunch window (attendance._day_lunch_hours).
Run: bench --site <site> execute frontier_hr.overtime_extension.overrides.test_lunch_window.run
"""

import frappe
from frappe.utils import get_datetime

from frontier_hr.overtime_extension.overrides import attendance as att
from frontier_hr.overtime_extension.overrides.attendance import (
	EXCLUDES_BREAKS,
	_effective_from_punches,
)


def _hours(in_t, out_t, shift, date="2026-09-01", out_date=None):
	doc = frappe._dict(attendance_date=date, in_time=f"{date} {in_t}", out_time=f"{out_date or date} {out_t}")
	return round(_effective_from_punches(doc, shift), 2)


def _hours_with_pairs(pairs, shift, date="2026-09-01"):
	"""Same, for a shift that measures each punch PAIR. `pairs` is a list of
	(in, out) clock strings; _punch_pairs is stubbed so the check needs no
	Employee Checkin rows."""
	real = att._punch_pairs
	att._punch_pairs = lambda doc, shift=None: [
		(get_datetime(f"{date} {a}"), get_datetime(f"{date} {b}")) for a, b in pairs
	]
	try:
		doc = frappe._dict(
			attendance_date=date,
			employee="_TEST",
			in_time=f"{date} {pairs[0][0]}",
			out_time=f"{date} {pairs[-1][1]}",
		)
		return round(_effective_from_punches(doc, shift), 2)
	finally:
		att._punch_pairs = real


def run():
	# --- window, measured first-in to last-out ---------------------------
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

	# --- window, measured per punch PAIR ---------------------------------
	paired = frappe._dict(day, working_hours_calculation_based_on=EXCLUDES_BREAKS)

	# Never punched out for lunch: hrms excluded no gap, so the break comes off
	# here. This is the case that used to slip through untouched.
	assert _hours_with_pairs([("09:00:00", "18:00:00")], paired) == 8.5

	# Punched out over lunch: hrms already dropped the gap. Taking the break
	# again would charge lunch twice.
	assert _hours_with_pairs([("09:00:00", "13:00:00"), ("13:30:00", "18:00:00")], paired) == 8.5

	# Punched out for only part of the window. On site 8.75h, of which 15 min
	# (13:00-13:15) fell inside lunch, so 15 min comes off and their own
	# 13:15-13:30 gap was already excluded by hrms. Same 8.5 as everyone else.
	assert _hours_with_pairs([("09:00:00", "13:15:00"), ("13:30:00", "18:00:00")], paired) == 8.5

	# A break outside the lunch window is the employee's own time, already
	# excluded by hrms, and does not earn back any of the lunch break.
	assert _hours_with_pairs([("09:00:00", "11:00:00"), ("11:30:00", "18:00:00")], paired) == 8.0

	# --- no window, measured per punch pair ------------------------------
	paired_flat = frappe._dict(start_time="09:00:00", custom_lunch_break_minutes=30,
		working_hours_calculation_based_on=EXCLUDES_BREAKS)
	assert _hours_with_pairs([("09:00:00", "18:00:00")], paired_flat) == 8.5  # no gap -> full break off
	assert _hours_with_pairs([("09:00:00", "13:00:00"), ("13:30:00", "18:00:00")], paired_flat) == 8.5  # gap covered it
	assert _hours_with_pairs([("09:00:00", "13:00:00"), ("13:15:00", "18:00:00")], paired_flat) == 8.5  # 15 gone, 15 taken

	# The invariant behind all of it: the same time actually on site is worth
	# the same pay, whichever way the shift measures it and whether or not the
	# employee bothered to punch out for lunch.
	assert (
		_hours("09:00:00", "18:00:00", day)
		== _hours_with_pairs([("09:00:00", "18:00:00")], paired)
		== _hours_with_pairs([("09:00:00", "13:00:00"), ("13:30:00", "18:00:00")], paired)
		== _hours_with_pairs([("09:00:00", "18:00:00")], paired_flat)
	)

	print("lunch window: all checks passed")
