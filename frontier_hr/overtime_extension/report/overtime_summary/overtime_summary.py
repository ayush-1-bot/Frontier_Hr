# Copyright (c) 2026, Frontier Softech
# For license information, please see license.txt

import calendar
from collections import defaultdict
from datetime import date, timedelta

import frappe
from frappe import _
from frappe.utils import add_days, add_months, flt, getdate

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

DEFAULT_STATUSES = ["Present", "Half Day", "Work From Home"]


def _week_start(d):
	"""Sunday of the Sun-Sat week containing `d`. Kept identical to
	overtime_extension.tasks._week_range so report buckets and threshold
	alerts agree on week boundaries."""
	return d - timedelta(days=(d.weekday() + 1) % 7)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	view = filters.get("view_by") or "Day"
	from_date, to_date = _resolve_range(view, filters)

	rows_raw = _fetch(filters, from_date, to_date)
	buckets = _build_buckets(view, from_date, to_date, filters)
	columns = _build_columns(view, buckets)
	data = _pivot(rows_raw, buckets, view, from_date, to_date)
	return columns, data


def _resolve_range(view, filters):
	if view == "Day":
		month = filters.get("month")
		year = int(filters.get("year") or date.today().year)
		if not month or month not in MONTHS:
			frappe.throw(_("Month is required for Day view"))
		m = MONTHS.index(month) + 1
		start = date(year, m, 1)
		end = date(year, m, calendar.monthrange(year, m)[1])
		return start, end

	if view == "Week":
		fd = filters.get("from_date")
		td = filters.get("to_date")
		if not fd or not td:
			frappe.throw(_("From Date and To Date required for Week view"))
		return getdate(fd), getdate(td)

	if view == "Quarter":
		pp = filters.get("payroll_period")
		if not pp:
			frappe.throw(_("Payroll Period required for Quarter view"))
		pp_doc = frappe.get_cached_doc("Payroll Period", pp)
		return getdate(pp_doc.start_date), getdate(pp_doc.end_date)

	frappe.throw(_("Unknown view: {0}").format(view))


def _fetch(filters, from_date, to_date):
	conds = ["a.docstatus < 2", "a.attendance_date BETWEEN %(fd)s AND %(td)s",
	         "a.company = %(company)s"]
	params = {"fd": from_date, "td": to_date, "company": filters.get("company")}

	statuses = filters.get("status") or DEFAULT_STATUSES
	if statuses:
		conds.append("a.status IN %(statuses)s")
		params["statuses"] = tuple(statuses)

	for key, col in [("department", "a.department"),
	                 ("employee", "a.employee"),
	                 ("branch", "e.branch"),
	                 ("designation", "e.designation")]:
		val = filters.get(key)
		if val:
			conds.append(f"{col} IN %({key})s")
			params[key] = tuple(val) if isinstance(val, list) else (val,)

	sql = f"""
		SELECT a.employee, a.employee_name, a.department,
		       a.attendance_date, a.custom_total_ot_hours
		FROM `tabAttendance` a
		LEFT JOIN `tabEmployee` e ON e.name = a.employee
		WHERE {' AND '.join(conds)}
		  AND IFNULL(a.custom_total_ot_hours, 0) > 0
	"""
	return frappe.db.sql(sql, params, as_dict=True)


def _build_buckets(view, from_date, to_date, filters):
	if view == "Day":
		last_day = calendar.monthrange(from_date.year, from_date.month)[1]
		return [("d" + str(d), str(d)) for d in range(1, last_day + 1)]

	if view == "Week":
		buckets = []
		week_start = _week_start(from_date)
		while week_start <= to_date:
			week_end = week_start + timedelta(days=6)
			clip_start = max(week_start, from_date)
			clip_end = min(week_end, to_date)
			# ISO weeks are Mon-Sun, so take the week number of the Wednesday
			# inside this Sun-Sat span — the ISO week it overlaps by 4 days.
			iso_week = (week_start + timedelta(days=3)).isocalendar()[1]
			key = "w_" + week_start.isoformat()
			label = "Week {0} ({1} – {2})".format(
				iso_week,
				clip_start.strftime("%d-%b"),
				clip_end.strftime("%d-%b"),
			)
			buckets.append((key, label))
			week_start += timedelta(days=7)
		return buckets

	if view == "Quarter":
		buckets = []
		q_start = from_date
		for i in range(4):
			q_end = add_days(add_months(q_start, 3), -1)
			buckets.append(("q" + str(i + 1), "Q{0} ({1} – {2})".format(
				i + 1,
				q_start.strftime("%b-%y"),
				q_end.strftime("%b-%y"),
			)))
			q_start = add_days(q_end, 1)
		return buckets

	return []


def _build_columns(view, buckets):
	cols = [
		{"label": _("Employee"), "fieldname": "employee",
		 "fieldtype": "Link", "options": "Employee", "width": 120},
		{"label": _("Employee Name"), "fieldname": "employee_name",
		 "fieldtype": "Data", "width": 180},
		{"label": _("Department"), "fieldname": "department",
		 "fieldtype": "Link", "options": "Department", "width": 140},
	]
	width = 90 if view == "Day" else 170
	for key, label in buckets:
		cols.append({"label": label, "fieldname": key,
		             "fieldtype": "Float", "precision": 2, "width": width})
	cols.append({"label": _("Total"), "fieldname": "total",
	             "fieldtype": "Float", "precision": 2, "width": 100})
	return cols


def _bucket_key(view, attendance_date, from_date):
	d = getdate(attendance_date)
	if view == "Day":
		return "d" + str(d.day)
	if view == "Week":
		return "w_" + _week_start(d).isoformat()
	if view == "Quarter":
		months_diff = (d.year - from_date.year) * 12 + (d.month - from_date.month)
		q_idx = months_diff // 3
		if q_idx < 0 or q_idx > 3:
			return None
		return "q" + str(q_idx + 1)
	return None


def _pivot(rows_raw, buckets, view, from_date, to_date):
	bucket_keys = set(k for k, _l in buckets)
	agg = defaultdict(lambda: {"employee": None, "employee_name": None,
	                            "department": None,
	                            **{k: 0.0 for k in bucket_keys},
	                            "total": 0.0})
	for r in rows_raw:
		key = _bucket_key(view, r.attendance_date, from_date)
		if not key or key not in bucket_keys:
			continue
		row = agg[r.employee]
		row["employee"] = r.employee
		row["employee_name"] = r.employee_name
		row["department"] = r.department
		hrs = flt(r.custom_total_ot_hours)
		row[key] += hrs
		row["total"] += hrs

	out = [row for row in agg.values() if row["total"] > 0]
	out.sort(key=lambda x: (x["department"] or "", x["employee_name"] or ""))
	return out
