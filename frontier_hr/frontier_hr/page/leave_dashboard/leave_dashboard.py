import frappe
from frappe import _
from frappe.utils import getdate, today

HR_ROLES = {"HR Manager", "HR User", "System Manager"}
FIELDS = ["name", "employee", "employee_name", "department", "leave_type", "from_date", "to_date",
          "total_leave_days", "half_day", "status", "docstatus"]


def _scope():
    """None = HR: rely on standard permissions (incl. company User Permissions).
    Otherwise the employees under the user in the reporting tree, plus those whose leave they approve."""
    if HR_ROLES & set(frappe.get_roles()):
        return None
    user = frappe.session.user
    me = frappe.db.get_value("Employee", {"user_id": user}, ["lft", "rgt"], as_dict=True)
    emps = set()
    if me and me.lft:
        emps.update(frappe.get_all("Employee", {"lft": (">", me.lft), "rgt": ("<", me.rgt)}, pluck="name"))
    emps.update(frappe.get_all("Leave Application", {"leave_approver": user}, pluck="employee", distinct=True))
    if not emps:
        frappe.throw(_("Only HR or reporting managers can view this dashboard."), frappe.PermissionError)
    return list(emps)


def _fetch(filters, start, end, scope, extra=None):
    f = [["from_date", "<=", end], ["to_date", ">=", start]]
    f += [[k, "=", filters[k]] for k in ("employee", "department", "leave_type", "status") if filters.get(k)]
    f += extra or []
    if scope is None:
        return frappe.get_list("Leave Application", filters=f, fields=FIELDS, order_by="from_date", limit_page_length=0)
    f.append(["employee", "in", scope])
    return frappe.get_all("Leave Application", filters=f, fields=FIELDS, order_by="from_date", limit_page_length=0)


@frappe.whitelist()
def get_dashboard_data(start: str, end: str, filters: dict | str | None = None):
    """start/end: calendar's visible range (end inclusive). Date-range filter narrows both events and cards."""
    filters = frappe.parse_json(filters or {})
    scope = _scope()
    start, end = getdate(start), getdate(end)
    f_from, f_to = getdate(filters.get("from_date") or start), getdate(filters.get("to_date") or end)

    ev_start, ev_end = max(start, f_from), min(end, f_to)
    events = _fetch(filters, ev_start, ev_end, scope) if ev_start <= ev_end else []

    # cards: the date-range filter if set, else the visible calendar range
    rows = _fetch(filters, f_from, f_to, scope) if (filters.get("from_date") or filters.get("to_date")) else events
    count = lambda s: sum(r.status == s for r in rows)
    t = today()
    on_leave = _fetch({**filters, "status": "Approved"}, t, t, scope, [["docstatus", "=", 1]])

    return {
        "events": events,
        "summary": {
            "total": len(rows), "open": count("Open"), "approved": count("Approved"),
            "rejected": count("Rejected"), "on_leave_today": len({r.employee for r in on_leave}),
        },
    }
