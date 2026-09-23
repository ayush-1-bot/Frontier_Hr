"""Shift Type lunch window (custom_lunch_start / custom_lunch_end).

With both times set, the lunch break is deducted from a day's hours only when
the employee covered the whole window — see attendance._day_lunch_hours. The
window's length is copied into custom_lunch_break_minutes here, so every
shift-level figure (credited leave/WFH day, OT rate, Salary Slip divisor),
which all read that field, stays equal to the window.
"""

import frappe
from frappe import _
from frappe.utils import to_timedelta


def sync_lunch_minutes(doc, method=None):
	start, end = doc.get("custom_lunch_start"), doc.get("custom_lunch_end")
	if not start and not end:
		return
	if not (start and end):
		frappe.throw(_("Set both Lunch Start and Lunch End, or leave both empty."))

	minutes = int((to_timedelta(end) - to_timedelta(start)).total_seconds() // 60) % 1440
	if not minutes:
		frappe.throw(_("Lunch End must be different from Lunch Start."))
	doc.custom_lunch_break_minutes = minutes
