__version__ = "0.0.1"


def _patch_hrms_leave_balance():
	"""Make hrms count the leave deducted from Attendance — see
	deduction_policy/overrides/leave_balance.py.

	Done here because frappe imports this package before any app module, so it
	lands in web workers, background jobs and bench commands alike. Never fatal:
	hrms may be absent (it is only required at runtime) or may have moved the
	function, and neither should stop the app loading.
	"""
	try:
		from frontier_hr.deduction_policy.overrides.leave_balance import apply

		apply()
	except Exception:
		pass


_patch_hrms_leave_balance()
