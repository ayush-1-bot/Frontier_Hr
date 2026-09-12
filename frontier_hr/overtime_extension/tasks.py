"""
Scheduled Tasks
---------------
daily_ot_threshold_check    - daily: scans Attendance for employees crossing threshold %
weekly_ot_threshold_check   - every Monday 9 AM: weekly threshold check
weekly_ot_summary           - every Monday 9 AM: paid OT summary from Overtime Slips
quarterly_ot_threshold_check- 1st of Jan/Apr/Jul/Oct: quarterly threshold check
"""

import frappe
from frappe.utils import (
    nowdate, getdate, add_days, flt
)


def _week_range(ref_date):
    """Return (Sunday, Saturday) of the week containing ref_date.
    Aligned with Frappe's get_first_day_of_week (Sun-Sat) so slip-submit
    alerts and scheduled alerts use identical week boundaries."""
    days_since_sunday = (ref_date.weekday() + 1) % 7
    sunday = add_days(ref_date, -days_since_sunday)
    saturday = add_days(sunday, 6)
    return sunday, saturday


# ---------------------------------------------------------------------------
# Dedup — one alert per (employee, ot_type, scope, period, stage)
# ---------------------------------------------------------------------------
# Uses Redis cache with TTL = seconds till period end + 1 day buffer.
# `stage` separates the in-period "warning" (fired by daily task) from the
# end-of-period "final" digest (fired by weekly/quarterly cron) so both can
# reach the recipient without one suppressing the other.

def _dedup_key(ot_type, scope, period_start, employee, stage="warning"):
    return f"ot_alert:{stage}:{ot_type}:{scope}:{period_start}:{employee}"


def _already_notified(ot_type, scope, period_start, employee, stage="warning"):
    return bool(frappe.cache().get_value(
        _dedup_key(ot_type, scope, period_start, employee, stage)
    ))


def _mark_notified(ot_type, scope, period_start, period_end, employee, stage="warning"):
    ttl = (getdate(period_end) - getdate(nowdate())).days * 86400 + 86400
    if ttl < 86400:
        ttl = 86400
    frappe.cache().set_value(
        _dedup_key(ot_type, scope, period_start, employee, stage),
        "1",
        expires_in_sec=ttl,
    )
from frontier_hr.overtime_extension.overrides.overtime_slip import (
    _get_quarter_range,
    _get_employee_ot_hours,
    _fiscal_year_start_month,
)


# ---------------------------------------------------------------------------
# Attendance-based OT aggregation
# ---------------------------------------------------------------------------

def _get_attendance_ot_hours(employee, from_date, to_date):
    result = frappe.db.sql(
        """
        SELECT COALESCE(SUM(custom_total_ot_hours), 0)
        FROM `tabAttendance`
        WHERE employee = %(employee)s
          AND attendance_date BETWEEN %(from_date)s AND %(to_date)s
          AND docstatus = 1
        """,
        {"employee": employee, "from_date": from_date, "to_date": to_date},
    )
    return flt(result[0][0]) if result else 0.0


# ---------------------------------------------------------------------------
# Daily threshold check
# ---------------------------------------------------------------------------

def daily_ot_threshold_check():
    today = getdate(nowdate())
    week_start, week_end = _week_range(today)
    q_start, q_end = _get_quarter_range(today)

    ot_types = frappe.get_all(
        "Overtime Type",
        fields=[
            "name",
            "weekly_max_ot_hours",
            "quarterly_max_ot_hours",
            "maximum_overtime_hours_allowed",
            "alert_threshold_percent",
        ],
    )

    employees = frappe.get_all(
        "Employee", filters={"status": "Active"}, fields=["name", "employee_name"]
    )

    for ot in ot_types:
        threshold = flt(ot.alert_threshold_percent)
        if not threshold:
            continue

        emails = _get_ot_type_recipients(ot.name)
        if not emails:
            continue

        daily_cap     = flt(ot.maximum_overtime_hours_allowed)
        weekly_cap    = flt(ot.weekly_max_ot_hours)
        quarterly_cap = flt(ot.quarterly_max_ot_hours)

        flagged = []
        notified_marks = []  # (scope, period_start, period_end, employee_id)

        def _consider(scope, cap, hours, period_start, period_end, emp_id, emp_name):
            if not cap or hours <= 0:
                return
            pct = (hours / cap) * 100
            if pct < threshold:
                return
            if _already_notified(ot.name, scope, period_start, emp_id, stage="warning"):
                return
            flagged.append({
                "employee": emp_name,
                "scope":    scope,
                "consumed": round(hours, 2),
                "cap":      cap,
                "percent":  round(pct, 1),
            })
            notified_marks.append((scope, period_start, period_end, emp_id))

        for emp in employees:
            if daily_cap:
                _consider("Daily", daily_cap,
                          _get_attendance_ot_hours(emp.name, today, today),
                          today, today, emp.name, emp.employee_name)
            if weekly_cap:
                _consider("Weekly", weekly_cap,
                          _get_attendance_ot_hours(emp.name, week_start, week_end),
                          week_start, week_end, emp.name, emp.employee_name)
            if quarterly_cap:
                _consider("Quarterly", quarterly_cap,
                          _get_attendance_ot_hours(emp.name, q_start, q_end),
                          q_start, q_end, emp.name, emp.employee_name)

        _send_digest(emails, ot.name, flagged, period_label=f"Daily {today}")
        for scope, ps, pe, eid in notified_marks:
            _mark_notified(ot.name, scope, ps, pe, eid, stage="warning")
        frappe.db.commit()


# ---------------------------------------------------------------------------
# Weekly + Quarterly threshold checks
# ---------------------------------------------------------------------------

def weekly_ot_threshold_check():
    # Runs Monday 9 AM — evaluate the Sun-Sat week that just ended.
    today = getdate(nowdate())
    this_sunday, _ = _week_range(today)  # current week's Sunday
    last_week_start = add_days(this_sunday, -7)   # previous Sunday
    last_week_end   = add_days(this_sunday, -1)   # previous Saturday
    _run_threshold_check(scope="Weekly", from_date=last_week_start, to_date=last_week_end)


def quarterly_ot_threshold_check():
    # Cron fires 1st of every month 09:00; self-gates on fiscal-quarter boundary.
    # Fiscal quarter start months = FY start + 0/3/6/9 (mod 12).
    today = getdate(nowdate())
    if today.day != 1:
        return
    fy_month = _fiscal_year_start_month()
    quarter_start_months = {((fy_month - 1 + i * 3) % 12) + 1 for i in range(4)}
    if today.month not in quarter_start_months:
        return
    prev_quarter_day = add_days(_get_quarter_range(today)[0], -1)
    q_start, q_end = _get_quarter_range(prev_quarter_day)
    _run_threshold_check(scope="Quarterly", from_date=q_start, to_date=q_end)


def _run_threshold_check(scope, from_date, to_date):
    """End-of-period 'final' digest — uses stage='final' dedup so it fires
    independently of the in-period 'warning' digest emitted by the daily task."""
    ot_types = frappe.get_all(
        "Overtime Type",
        fields=["name", "weekly_max_ot_hours", "quarterly_max_ot_hours", "alert_threshold_percent"],
    )

    employees = frappe.get_all(
        "Employee", filters={"status": "Active"}, fields=["name", "employee_name"]
    )

    for ot in ot_types:
        threshold = flt(ot.alert_threshold_percent)
        cap = flt(ot.weekly_max_ot_hours if scope == "Weekly" else ot.quarterly_max_ot_hours)
        if not threshold or not cap:
            continue

        emails = _get_ot_type_recipients(ot.name)
        if not emails:
            continue

        flagged = []
        notified_marks = []
        for emp in employees:
            hours = _get_attendance_ot_hours(emp.name, from_date, to_date)
            if not hours:
                continue
            pct = (hours / cap) * 100
            if pct < threshold:
                continue
            if _already_notified(ot.name, scope, from_date, emp.name, stage="final"):
                continue
            notified_marks.append(emp.name)
            flagged.append({
                    "employee": emp.employee_name,
                    "scope":    scope,
                    "consumed": round(hours, 2),
                    "cap":      cap,
                    "percent":  round(pct, 1),
                })

        _send_digest(emails, ot.name, flagged, period_label=f"{scope} {from_date} to {to_date}")
        for eid in notified_marks:
            _mark_notified(ot.name, scope, from_date, to_date, eid, stage="final")
        frappe.db.commit()


# ---------------------------------------------------------------------------
# Weekly paid OT summary
# ---------------------------------------------------------------------------

# def weekly_ot_summary():
#     today           = getdate(nowdate())
#     last_week_end   = add_days(today, -1)
#     last_week_start = add_days(last_week_end, -6)

#     employees = frappe.get_all("Employee", filters={"status": "Active"}, fields=["name", "employee_name"])

#     summary = []
#     for emp in employees:
#         hours = _get_employee_ot_hours(emp.name, last_week_start, last_week_end)
#         if hours > 0:
#             summary.append({
#                 "employee": emp.employee_name,
#                 "hours":    round(hours, 2),
#             })

#     if not summary:
#         return

#     # Resolve recipients to emails
#     all_recipients_raw = frappe.db.sql_list(
#         "SELECT DISTINCT user FROM `tabOT Alert Recipient` WHERE parenttype = 'Overtime Type'"
#     )
#     all_recipients = []
#     for u in all_recipients_raw:
#         if not u:
#             continue
#         email = u if "@" in u else frappe.db.get_value("User", u, "email")
#         if email:
#             all_recipients.append(email)
    
#     if not all_recipients:
#         return

#     rows = "".join([
#         f"<tr><td>{s['employee']}</td><td>{s['hours']}</td></tr>"
#         for s in sorted(summary, key=lambda x: -x["hours"])
#     ])
#     message = f"""
#     <p>Weekly OT Summary (Paid): {last_week_start} to {last_week_end}</p>
#     <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;">
#       <thead style="background:#f4f4f4;">
#         <tr><th>Employee</th><th>Payable OT Hours</th></tr>
#       </thead>
#       <tbody>{rows}</tbody>
#     </table>
#     """
#     frappe.sendmail(
#         recipients=all_recipients,
#         subject=f"Weekly OT Summary ({last_week_start} - {last_week_end})",
#         message=message,
#     )
#     frappe.db.commit()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _get_ot_type_recipients(ot_type_name):
    """Get recipients for OT Type — resolves User to email automatically."""
    rows = frappe.db.sql(
        "SELECT user FROM `tabOT Alert Recipient` "
        "WHERE parent = %s AND parenttype = 'Overtime Type'",
        ot_type_name, as_dict=True,
    )
    emails = []
    for r in rows:
        if not r.user:
            continue
        # If already an email, use directly; else lookup User's email
        email = r.user if "@" in r.user else frappe.db.get_value("User", r.user, "email")
        if email:
            emails.append(email)
    return emails


def _send_digest(emails, ot_type_name, flagged, period_label=None):
    header = f"<p>OT Threshold Digest for <b>{ot_type_name}</b> on {nowdate()}"
    if period_label:
        header += f" — <i>{period_label}</i>"
    header += ".</p>"
    if flagged:
        rows = "".join([
            f"<tr><td>{f['employee']}</td><td>{f['scope']}</td>"
            f"<td>{f['consumed']}</td><td>{f['cap']}</td>"
            f"<td><b>{f['percent']}%</b></td></tr>"
            for f in flagged
        ])
        body = f"""
        <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;">
          <thead style="background:#f4f4f4;">
            <tr><th>Employee</th><th>Scope</th><th>Consumed</th><th>Cap</th><th>% Used</th></tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
        """
        subject_suffix = f"{len(flagged)} flagged"
    else:
        body = "<p><b>No employees crossed the alert threshold in this window.</b></p>"
        subject_suffix = "0 flagged"
    frappe.sendmail(
        recipients=emails,
        subject=f"OT Threshold Digest [{ot_type_name}] - {nowdate()} ({subject_suffix})",
        message=header + body,
    )
# =========================================================
# Miss Punch Notifications
# =========================================================
"""
Miss Punch Notifications
------------------------
Runs hourly via scheduler. When the current hour:minute matches the configured
send time (and hasn't already run today), it:

  1. Finds yesterday's Attendance records with missing in_time or out_time
     (excluding approved leaves).
  2. Sends each affected employee an individual email with only their record.
  3. Sends a consolidated report to configured recipients.

Idempotency: last_run_date is stored on the Single doctype to prevent duplicate
sends if the scheduler fires more than once in the target window.
"""

import frappe
from frappe.utils import add_days, today, now_datetime, get_datetime


def run_if_due():
	"""Scheduler entrypoint. Called every hour (see hooks.py)."""
	settings = frappe.get_single("Miss Punch Report Settings")

	if not settings.enabled:
		return

	now = now_datetime()
	send_hour = int(settings.send_hour or 9)
	send_minute = int(settings.send_minute or 0)

	# Only run at or after the configured time, once per day
	if now.hour < send_hour or (now.hour == send_hour and now.minute < send_minute):
		return

	last_run = settings.get("last_run_date")
	if last_run and get_datetime(last_run).date() == now.date():
		return

	try:
		process_miss_punches(settings)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Miss Punch Notification Failed")
		raise
	else:
		# Only mark run on success so a failure can retry on the next hourly tick
		frappe.db.set_value(
			"Miss Punch Report Settings", None, "last_run_date", now, update_modified=False
		)
		frappe.db.commit()


def process_miss_punches(settings):
	yesterday = add_days(today(), -1)
	records = get_miss_punch_records(yesterday)

	if settings.notify_employee and records:
		send_employee_emails(records, yesterday)

	recipients = [r.user for r in (settings.recipients or []) if r.user]
	if recipients:
		if records or settings.send_empty_report:
			send_consolidated_email(recipients, records, yesterday)


def get_miss_punch_records(date):
	"""
	Return list of dicts for employees with miss punch on `date`.

	A miss punch = Attendance exists with exactly ONE of in_time/out_time
	missing (a genuine partial punch), and the employee is NOT on approved
	leave that day. Records missing BOTH in_time and out_time are excluded —
	that's a plain absence with no punch data at all, not a miss punch.
	"""
	return frappe.db.sql(
		"""
		SELECT
			a.name AS attendance,
			a.employee,
			a.employee_name,
			a.department,
			a.company,
			a.in_time,
			a.out_time,
			a.status,
			e.user_id,
			e.company_email,
			e.personal_email
		FROM `tabAttendance` a
		INNER JOIN `tabEmployee` e ON e.name = a.employee
		WHERE a.attendance_date = %(date)s
		  AND a.docstatus < 2
		  AND e.status = 'Active'
		  AND (a.in_time IS NULL) != (a.out_time IS NULL)
		  AND NOT EXISTS (
			  SELECT 1 FROM `tabLeave Application` la
			  WHERE la.employee = a.employee
			    AND la.status = 'Approved'
			    AND la.docstatus = 1
			    AND %(date)s BETWEEN la.from_date AND la.to_date
		  )
		ORDER BY a.department, a.employee_name
		""",
		{"date": date},
		as_dict=True,
	)


def _employee_email(row):
	return row.get("company_email") or row.get("user_id") or row.get("personal_email")


def send_employee_emails(records, date):
	for row in records:
		email = _employee_email(row)
		if not email:
			continue

		message = f"""
			<p>Hi {frappe.utils.escape_html(row.employee_name or '')},</p>
			<p>Our records show a <b>miss punch</b> on <b>{date}</b>:</p>
			<table border="1" cellpadding="6" cellspacing="0"
				style="border-collapse:collapse;">
				<tr><th>Date</th><td>{date}</td></tr>
				<tr><th>Check-In</th><td>{row.in_time or '<b style="color:#c0392b;">Missing</b>'}</td></tr>
				<tr><th>Check-Out</th><td>{row.out_time or '<b style="color:#c0392b;">Missing</b>'}</td></tr>
				<tr><th>Status</th><td>{row.status or '-'}</td></tr>
			</table>
			<p>Please raise an Attendance Regularization request if this is incorrect.</p>
			<p>Regards,<br>HR</p>
		"""

		try:
			frappe.sendmail(
				recipients=[email],
				subject=f"Miss Punch Alert — {date}",
				message=message,
				reference_doctype="Attendance",
				reference_name=row.attendance,
				now=False,
			)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"Miss Punch employee mail failed: {row.employee} <{email}>",
			)


def send_consolidated_email(recipients, records, date):
	if records:
		rows_html = "".join(
			f"""
			<tr>
				<td>{i}</td>
				<td>{frappe.utils.escape_html(r.employee)}</td>
				<td>{frappe.utils.escape_html(r.employee_name or '')}</td>
				<td>{frappe.utils.escape_html(r.department or '-')}</td>
				<td>{r.in_time or '<span style="color:#c0392b;">Missing</span>'}</td>
				<td>{r.out_time or '<span style="color:#c0392b;">Missing</span>'}</td>
				<td>{r.status or '-'}</td>
			</tr>
			"""
			for i, r in enumerate(records, 1)
		)
		message = f"""
			<h3>Miss Punch Consolidated Report — {date}</h3>
			<p>Total records: <b>{len(records)}</b></p>
			<table border="1" cellpadding="6" cellspacing="0"
				style="border-collapse:collapse; font-size: 13px;">
				<thead style="background:#f4f6f8;">
					<tr>
						<th>#</th><th>Employee ID</th><th>Name</th>
						<th>Department</th><th>Check-In</th>
						<th>Check-Out</th><th>Status</th>
					</tr>
				</thead>
				<tbody>{rows_html}</tbody>
			</table>
			<p style="color:#666; font-size:12px;">
				Auto-generated by Frontier Softech.
			</p>
		"""
	else:
		message = f"""
			<h3>Miss Punch Consolidated Report — {date}</h3>
			<p>No miss punch records found for <b>{date}</b>.</p>
		"""

	frappe.sendmail(
		recipients=recipients,
		subject=f"Miss Punch Report — {date} ({len(records)} records)",
		message=message,
		now=False,
	)
