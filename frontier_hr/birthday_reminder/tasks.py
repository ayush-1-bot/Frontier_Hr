"""Branch-scoped birthday reminders.

Replaces hrms.controllers.employee_reminders.send_birthday_reminders, which
mails every active employee of the company. Here a birthday is announced only
to active employees of the same company AND the same branch. Employees with no
branch form their own group (blank branch == blank branch).

Toggle: HR Settings -> Birthdays Branch Wise (custom_branch_wise_birthday_reminders).
Off = falls back to the stock hrms company-wide mail. The hrms job itself is
stopped by `stop_hrms_birthday_job` (after_migrate), so either way only this job
sends. Still gated by HR Settings -> Birthdays (send_birthday_reminders).
Mail text and template are reused from hrms unchanged.
"""

from collections import defaultdict

import frappe

from hrms.controllers.employee_reminders import (
	get_birthday_reminder_text_and_message,
	get_sender_email,
	send_birthday_reminder,
	send_birthday_reminders,
)

HRMS_BIRTHDAY_JOB = "hrms.controllers.employee_reminders.send_birthday_reminders"


def send_branch_birthday_reminders():
	if not int(frappe.db.get_single_value("HR Settings", "custom_branch_wise_birthday_reminders") or 0):
		# toggle off: stock company-wide behaviour (hrms job itself stays stopped)
		return send_birthday_reminders()
	if not int(frappe.db.get_single_value("HR Settings", "send_birthday_reminders") or 0):
		return

	sender = get_sender_email()
	for (company, branch), birthday_persons in get_birthdays_by_branch().items():
		birthday_emails = {_email(p) for p in birthday_persons}
		recipients = list(set(get_branch_employee_emails(company, branch)) - birthday_emails)

		if recipients:
			reminder_text, message = get_birthday_reminder_text_and_message(birthday_persons)
			send_birthday_reminder(recipients, reminder_text, birthday_persons, message, sender)

		if len(birthday_persons) > 1:
			# same as hrms: people sharing a birthday get told about each other
			for person in birthday_persons:
				if not _email(person):
					continue
				others = [d for d in birthday_persons if d != person]
				reminder_text, message = get_birthday_reminder_text_and_message(others)
				send_birthday_reminder(_email(person), reminder_text, others, message, sender)


def get_birthdays_by_branch(date=None):
	"""{(company, branch): [employee rows born on `date`]}. Branch is "" when unset."""
	rows = frappe.db.sql(
		"""
		SELECT `personal_email`, `company`, `company_email`, `user_id`,
			`employee_name` AS `name`, `image`, `date_of_joining`,
			IFNULL(`branch`, '') AS `branch`
		FROM `tabEmployee`
		WHERE DAY(`date_of_birth`) = DAY(%(date)s)
			AND MONTH(`date_of_birth`) = MONTH(%(date)s)
			AND YEAR(`date_of_birth`) < YEAR(%(date)s)
			AND `status` = 'Active'
		""",
		{"date": date or frappe.utils.today()},
		as_dict=True,
	)
	grouped = defaultdict(list)
	for row in rows:
		grouped[(row.company, row.branch)].append(row)
	return grouped


def get_branch_employee_emails(company, branch):
	employees = frappe.get_all(
		"Employee",
		filters={"status": "Active", "company": company, "branch": branch or ("is", "not set")},
		fields=["user_id", "company_email", "personal_email"],
	)
	return [e for e in (_email(emp) for emp in employees) if e]


def _email(row):
	# hrms get_all_employee_emails order: user_id, company_email, personal_email
	return row.get("user_id") or row.get("company_email") or row.get("personal_email")


def stop_hrms_birthday_job():
	"""after_migrate: stop the stock company-wide job so mails are not doubled.
	sync_jobs keeps `stopped` on existing Scheduled Job Types, so this sticks."""
	frappe.db.set_value("Scheduled Job Type", {"method": HRMS_BIRTHDAY_JOB}, "stopped", 1)
