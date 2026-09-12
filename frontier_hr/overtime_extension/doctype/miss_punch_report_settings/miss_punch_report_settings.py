import frappe
from frappe.model.document import Document


class MissPunchReportSettings(Document):
	def validate(self):
		if self.send_hour is not None and not (0 <= self.send_hour <= 23):
			frappe.throw("Send Hour must be between 0 and 23")
		if self.send_minute is not None and not (0 <= self.send_minute <= 59):
			frappe.throw("Send Minute must be between 0 and 59")
