# Copyright (c) 2026, Frontier Softech
# For license information, please see license.txt


def get_context(context):
	"""Frappe calls this for every standard web form.

	Nothing to add here: the quota banner is read in the browser instead,
	because what is left depends on the day the employee picks and that
	changes without reloading the page.
	"""
