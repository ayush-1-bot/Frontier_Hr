// The quota resets every period, so what is left depends on the day picked.
// Read it again whenever the date changes, the way the form itself would.
frappe.ready(() => {
	const banner = document.createElement("div");
	banner.className = "alert alert-info";
	banner.style.display = "none";

	const form = document.querySelector(".web-form-wrapper") || document.querySelector("main");
	if (form) form.prepend(banner);

	const show_balance = (date) => {
		frappe.call({
			method: "frontier_hr.short_leave.api.my_balance",
			args: { date: date || frappe.datetime.get_today() },
			callback: ({ message }) => {
				if (!message) {
					banner.style.display = "none";
					return;
				}
				banner.textContent = __("{0} of {1} short leaves left, {2} hours, until {3}", [
					message.remaining,
					message.allowed,
					message.remaining_hours,
					message.period_end,
				]);
				banner.style.display = "";
			},
		});
	};

	show_balance(frappe.web_form.get_value("date"));
	frappe.web_form.on("date", (field, value) => show_balance(value));
});
