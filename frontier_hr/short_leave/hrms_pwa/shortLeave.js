import { createResource } from "frappe-ui"
import { employeeResource } from "@/data/employee"

export const myShortLeaveRequests = createResource({
	url: "frontier_hr.short_leave.api.get_requests",
	params: {
		employee: employeeResource.data.name,
		limit: 10,
	},
	auto: true,
	cache: "hrms:my_short_leave_requests",
	transform(data) {
		return (data || []).map((request) => {
			request.doctype = "Short Leave Request"
			return request
		})
	},
})

// "17:30 - 18:20" from the two Time fields, which arrive as "HH:MM:SS".
export function getTimeRange(doc) {
	const trim = (t) => (t ? String(t).slice(0, 5) : "")
	return `${trim(doc.from_time)} - ${trim(doc.to_time)}`
}
