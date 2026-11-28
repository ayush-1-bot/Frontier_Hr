// Shared calendar dashboard used by the Leave and Attendance Request dashboard pages.
// Loaded on demand: frappe.require("/assets/frontier_hr/js/calendar_dashboard.js", ...)
frappe.provide("frontier_hr");

frontier_hr.CalendarDashboard = class CalendarDashboard {
	/**
	 * cfg: {
	 *   doctype, method,
	 *   statuses: { key: {label, color, text, indicator} },
	 *   filters: [{fieldname, label, fieldtype, options}]  (a "status" Select is added automatically),
	 *   cards: [[summary_key, label, color, status_key_or_null]],
	 *   status_of(row), title(row, date), tooltip(row), subtitle?(row, date),
	 *   columns: [[label, (row, date) => html]]   (day dialog; values must already be escaped),
	 *   split_days?: one event per date instead of one bar (so title/subtitle can differ per day)
	 * }
	 */
	constructor(page, cfg) {
		this.page = page;
		this.cfg = cfg;
		this.events = [];
		this.refresh = frappe.utils.debounce(() => this.calendar && this.calendar.refetchEvents(), 300);
		this.realtime_handler = (data) => data && data.doctype === cfg.doctype && this.refresh();
		this.make_filters();
		this.make_layout();
		frappe.require("calendar.bundle.js", () => this.make_calendar());
	}

	status(row) {
		return (
			this.cfg.statuses[this.cfg.status_of(row)] || {
				label: this.cfg.status_of(row),
				color: "#98a2b3",
				text: "#fff",
				indicator: "gray",
			}
		);
	}

	make_filters() {
		const status = {
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: [
				{ value: "", label: __("All Statuses") },
				...Object.entries(this.cfg.statuses).map(([value, s]) => ({ value, label: s.label })),
			],
		};
		const defs = [
			...this.cfg.filters,
			status,
			{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date" },
			{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date" },
		];
		this.filters = {};
		for (const d of defs)
			this.filters[d.fieldname] = this.page.add_field({ ...d, change: () => this.on_filter_change(d.fieldname) });
	}

	get_filters() {
		const out = {};
		for (const [k, field] of Object.entries(this.filters)) {
			const v = field.get_value();
			if (v) out[k] = v;
		}
		return out;
	}

	on_filter_change(fieldname) {
		const from = this.filters.from_date.get_value();
		if (fieldname === "from_date" && from && this.calendar) this.calendar.gotoDate(from);
		this.refresh();
	}

	make_layout() {
		const cards = this.cfg.cards;
		this.$body = $(`
			<div class="cal-dash">
				<div class="cd-cards">${cards
					.map(
						([key, label, color]) => `
						<div class="cd-card" style="border-left-color:${color}" data-key="${key}">
							<div class="cd-card-label">${label}</div>
							<div class="cd-card-value" data-value="${key}">–</div>
						</div>`
					)
					.join("")}
				</div>
				<div class="cd-legend">${Object.values(this.cfg.statuses)
					.map((s) => `<span><i style="background:${s.color}"></i>${s.label}</span>`)
					.join("")}
				</div>
				<div class="cd-calendar"></div>
			</div>`).appendTo(this.page.main);

		// clicking a status card filters by that status
		for (const [key, , , status] of cards) {
			if (status == null) continue;
			this.$body
				.find(`[data-key="${key}"]`)
				.css("cursor", "pointer")
				.on("click", () => this.filters.status.set_value(status));
		}
	}

	make_calendar() {
		this.calendar = new frappe.FullCalendar(this.$body.find(".cd-calendar")[0], {
			plugins: frappe.FullCalendar.Plugins,
			initialView: window.innerWidth < 768 ? "listMonth" : "dayGridMonth",
			headerToolbar: {
				left: "prev,next today",
				center: "title",
				right: "dayGridMonth,dayGridWeek,dayGridDay,listMonth",
			},
			buttonText: {
				today: __("Today"),
				month: __("Month"),
				week: __("Week"),
				day: __("Day"),
				list: __("List"),
			},
			height: "auto",
			dayMaxEvents: 4,
			firstDay: frappe.datetime.get_first_day_of_the_week_index(),
			events: (info, success, failure) => this.fetch(info, success, failure),
			eventClick: (info) => {
				info.jsEvent.preventDefault();
				this.open(info.event.id);
			},
			dateClick: (info) => this.show_day(info.dateStr),
			eventDidMount: (info) => (info.el.title = this.cfg.tooltip(info.event.extendedProps)),
			eventContent: (arg) => {
				const sub = arg.event.extendedProps._subtitle;
				if (!sub) return true; // default rendering
				const esc = frappe.utils.escape_html;
				return {
					html: `<div class="cd-ev"><div class="cd-ev-title">${esc(
						arg.event.title
					)}</div><div class="cd-ev-sub">${esc(sub)}</div></div>`,
				};
			},
		});
		this.calendar.render();
	}

	open(name) {
		frappe.set_route("Form", this.cfg.doctype, name);
	}

	fetch(info, success, failure) {
		frappe.call({
			method: this.cfg.method,
			args: {
				start: info.startStr.slice(0, 10),
				end: frappe.datetime.add_days(info.endStr.slice(0, 10), -1), // FullCalendar end is exclusive
				filters: this.get_filters(),
			},
			callback: (r) => {
				const { events, summary } = r.message;
				this.events = events;
				for (const [k, v] of Object.entries(summary)) this.$body.find(`[data-value="${k}"]`).text(v);
				success(events.flatMap((e) => this.to_events(e)));
			},
			error: failure,
		});
	}

	to_events(e) {
		const s = this.status(e);
		const ev = (start, end, date) => ({
			id: e.name,
			title: this.cfg.title(e, date),
			start,
			end: frappe.datetime.add_days(end, 1),
			allDay: true,
			backgroundColor: s.color,
			borderColor: s.color,
			textColor: s.text,
			extendedProps: { ...e, _subtitle: this.cfg.subtitle?.(e, date) },
		});
		if (!this.cfg.split_days) return [ev(e.from_date, e.to_date)];
		const out = [];
		for (let d = e.from_date; d <= e.to_date; d = frappe.datetime.add_days(d, 1)) out.push(ev(d, d, d));
		return out;
	}

	show_day(date) {
		const rows = this.events.filter((e) => e.from_date <= date && e.to_date >= date);
		const cols = [
			...this.cfg.columns,
			[
				__("Status"),
				(e) => `<span class="indicator-pill ${this.status(e).indicator}">${this.status(e).label}</span>`,
			],
		];
		const d = new frappe.ui.Dialog({
			title: __("{0} on {1}", [__(this.cfg.doctype), frappe.datetime.str_to_user(date)]),
			size: "extra-large",
		});
		d.$body.html(
			rows.length
				? `<div class="table-responsive"><table class="table table-hover"><thead><tr>${cols
						.map(([l]) => `<th>${l}</th>`)
						.join("")}</tr></thead>
					<tbody>${rows
						.map(
							(e) =>
								`<tr data-name="${frappe.utils.escape_html(e.name)}" style="cursor:pointer">${cols
									.map(([, fn]) => `<td>${fn(e, date)}</td>`)
									.join("")}</tr>`
						)
						.join("")}</tbody></table></div>`
				: `<div class="text-muted text-center p-4">${__("Nothing on this date.")}</div>`
		);
		const me = this;
		d.$body.on("click", "tr[data-name]", function () {
			d.hide();
			me.open(this.dataset.name);
		});
		d.show();
	}

	on_show() {
		// list views call realtime.off("list_update") on load, so re-attach every time the page is shown
		frappe.realtime.doctype_subscribe(this.cfg.doctype);
		frappe.realtime.off("list_update", this.realtime_handler);
		frappe.realtime.on("list_update", this.realtime_handler);
		this.refresh();
	}

	// common day-dialog cell: employee name + id
	static employee_cell(e) {
		const esc = frappe.utils.escape_html;
		return `<a>${esc(e.employee_name)}</a><div class="text-muted small">${esc(e.employee)}</div>`;
	}
};

frappe.dom.set_style(
	`
.cal-dash { padding: var(--padding-md) 0; }
.cd-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: var(--margin-md); margin-bottom: var(--margin-md); }
.cd-card { background: var(--card-bg); border: 1px solid var(--border-color); border-left: 4px solid; border-radius: var(--border-radius-md); padding: var(--padding-md); }
.cd-card-label { color: var(--text-muted); font-size: var(--text-sm); }
.cd-card-value { font-size: 1.75rem; font-weight: 600; color: var(--heading-color); }
.cd-legend { display: flex; flex-wrap: wrap; gap: var(--margin-md); margin-bottom: var(--margin-sm); font-size: var(--text-sm); color: var(--text-muted); }
.cd-legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 6px; }
.cd-calendar { background: var(--card-bg); border: 1px solid var(--border-color); border-radius: var(--border-radius-md); padding: var(--padding-md); }
.cd-calendar .fc-event { cursor: pointer; font-size: var(--text-xs); padding: 1px 4px; }
.cd-calendar .fc-daygrid-day { cursor: pointer; }
.cd-ev { overflow: hidden; white-space: normal; line-height: 1.25; }
.cd-ev-title { font-weight: 600; }
.cd-ev-sub { font-size: 0.9em; opacity: 0.9; }
@media (max-width: 767px) {
	.cd-calendar .fc-header-toolbar { flex-direction: column; gap: var(--margin-sm); }
	.cd-calendar .fc-toolbar-title { font-size: 1.1rem; }
}
`,
	"frontier-calendar-dashboard-style"
);
