"""Put the short leave screens back into the hrms mobile app.

The app at ``/hrms`` is a compiled Vue bundle that ships inside hrms, and its
Home screen and router are literal lists with no extension point, so these
screens can only exist as an edit to hrms itself. A bench update throws that
edit away. The edit is kept beside this file as three copies plus a patch;
this puts it back.

	bench execute frontier_hr.short_leave.hrms_pwa.apply.main

Rebuilding the bundle needs yarn, so that stays a separate step:

	cd apps/hrms/frontend && yarn build
"""

import os
import shutil
import subprocess

import frappe

BUILD_HINT = "cd apps/hrms/frontend && yarn build"
PATCH = "short_leave_pwa.patch"

# What goes where, relative to the hrms repository root.
COPIES = {
	"ShortLeaveRequestForm.vue": "frontend/src/views/leave",
	"ShortLeaveRequestItem.vue": "frontend/src/components",
	"shortLeave.js": "frontend/src/data",
}


def main():
	"""Copy the screens into hrms and apply the patch. Safe to re-run."""
	source, hrms = _paths()
	if not hrms:
		print("hrms is not on this bench, nothing to do")
		return
	if is_applied():
		print("already applied, nothing to do")
		return
	for name, target in COPIES.items():
		shutil.copy(os.path.join(source, name), os.path.join(hrms, target, name))
	applied = _git(hrms, "apply", os.path.join(source, PATCH))
	if applied.returncode:
		print(f"the patch did not apply:\n{applied.stderr}")
		print("hrms moved those lines. Make the six edits by hand, see README.md.")
		return
	print(f"applied. Now rebuild the bundle:\n\t{BUILD_HINT}")


def is_applied():
	"""True where the hrms tree already carries the short leave screens."""
	source, hrms = _paths()
	if not hrms:
		return True  # nothing to put back on a bench without hrms
	for name, target in COPIES.items():
		if not os.path.exists(os.path.join(hrms, target, name)):
			return False
	reverses = _git(hrms, "apply", "--reverse", "--check", os.path.join(source, PATCH))
	return reverses.returncode == 0


def warn_if_missing():
	"""after_migrate: say so where an hrms update dropped the screens."""
	if is_applied():
		return
	message = "frontier_hr: short leave is missing from the hrms mobile app"
	frappe.logger().warning(message)
	print(f"{message}. Put it back with:")
	print("\tbench execute frontier_hr.short_leave.hrms_pwa.apply.main")
	print(f"\t{BUILD_HINT}")


def _paths():
	"""Where the copies live, and the hrms repository root or None."""
	source = os.path.dirname(os.path.abspath(__file__))
	try:
		hrms = os.path.dirname(frappe.get_app_path("hrms"))
	except Exception:
		return source, None
	return source, hrms


def _git(hrms, *args):
	return subprocess.run(["git", *args], cwd=hrms, capture_output=True, text=True)
