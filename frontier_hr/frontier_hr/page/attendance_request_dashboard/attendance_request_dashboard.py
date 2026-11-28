import frappe
from collections import defaultdict

from frappe.utils import add_days, getdate, today

from frontier_hr.frontier_hr.page.leave_dashboard.leave_dashboard import _scope

# Attendance Request has no status field: draft = pending, submitted = approved (attendance created).
DOCSTATUS = {"Pending": 0, "Approved": 1, "Cancelled": 2}
FIELDS = ["name", "employee", "employee_name", "department", "reason", "from_date", "to_date", "half_day",
          "half_day_date", "shift", "explanation", "include_holidays", "docstatus"]


def _fetch(filters, start, end, scope, extra=None):
    f = [["from_date", "<=", end], ["to_date", ">=", start]]
    f += [[k, "=", filters[k]] for k in ("employee", "department", "reason") if filters.get(k)]
    if filters.get("status") in DOCSTATUS:
        f.append(["docstatus", "=", DOCSTATUS[filters["status"]]])
    f += extra or []
    get = frappe.get_list
    if scope is not None:
        f.append(["employee", "in", scope])
        get = frappe.get_all
    return get("Attendance Request", filters=f, fields=FIELDS, order_by="from_date", limit_page_length=0)


def _attach_punches(rows, start, end):
    """row.punches = {"YYYY-MM-DD": [["08:56", "IN"], ...]} for each request date that has Employee Checkins.
    A punch belongs to its shift's start date when set, so night-shift OUT punches stay on the shift day."""
    if not rows:
        return
    # +1 day: a night shift that started on `end` can punch out after midnight
    logs = frappe.get_all("Employee Checkin",
        filters={"employee": ["in", list({r.employee for r in rows})], "time": ["between", [start, add_days(end, 1)]]},
        fields=["employee", "time", "log_type", "shift_start"], order_by="time")
    by_day = defaultdict(list)
    for c in logs:
        by_day[(c.employee, str((c.shift_start or c.time).date()))].append([c.time.strftime("%H:%M"), c.log_type or ""])
    for r in rows:
        r.punches = {}
        d = r.from_date
        while d <= r.to_date:
            if p := by_day.get((r.employee, str(d))):
                r.punches[str(d)] = p
            d = add_days(d, 1)


@frappe.whitelist()
def get_dashboard_data(start: str, end: str, filters: dict | str | None = None):
    """start/end: calendar's visible range (end inclusive). Date-range filter narrows both events and cards."""
    filters = frappe.parse_json(filters or {})
    scope = _scope()
    start, end = getdate(start), getdate(end)
    f_from, f_to = getdate(filters.get("from_date") or start), getdate(filters.get("to_date") or end)

    ev_start, ev_end = max(start, f_from), min(end, f_to)
    events = _fetch(filters, ev_start, ev_end, scope) if ev_start <= ev_end else []
    _attach_punches(events, ev_start, ev_end)
    rows = _fetch(filters, f_from, f_to, scope) if (filters.get("from_date") or filters.get("to_date")) else events
    count = lambda ds: sum(r.docstatus == ds for r in rows)
    t = today()
    todays = _fetch(filters, t, t, scope, [["docstatus", "<", 2]])

    return {
        "events": events,
        "summary": {"total": len(rows), "pending": count(0), "approved": count(1), "cancelled": count(2), "today": len(todays)},
    }
