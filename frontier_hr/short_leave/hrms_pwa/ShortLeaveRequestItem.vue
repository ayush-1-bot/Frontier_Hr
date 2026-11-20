<template>
	<ListItem
		:isTeamRequest="props.isTeamRequest"
		:employee="props.doc.employee"
		:employeeName="props.doc.employee_name"
	>
		<template #left>
			<LeaveIcon class="h-5 w-5 text-gray-500" />
			<div class="flex flex-col items-start gap-1.5">
				<div class="text-base font-normal text-gray-800">
					{{ props.doc.reason }}
				</div>
				<div class="text-xs font-normal text-gray-500">
					<span>{{ dayjs(props.doc.date).format("D MMM") }}</span>
					<span class="whitespace-pre"> &middot; </span>
					<span class="whitespace-nowrap">{{ getTimeRange(props.doc) }}</span>
					<span class="whitespace-pre"> &middot; </span>
					<span class="whitespace-nowrap">{{ __("{0}h", [props.doc.hours]) }}</span>
				</div>
			</div>
		</template>
		<template #right>
			<Badge variant="outline" :theme="colorMap[status]" :label="__(status)" size="md" />
			<FeatherIcon name="chevron-right" class="h-5 w-5 text-gray-500" />
		</template>
	</ListItem>
</template>

<script setup>
import { computed, inject } from "vue"
import { Badge, FeatherIcon } from "frappe-ui"
import dayjs from "dayjs"

import ListItem from "@/components/ListItem.vue"
import LeaveIcon from "@/components/icons/LeaveIcon.vue"
import { getTimeRange } from "@/data/shortLeave"

const __ = inject("$translate")
const props = defineProps({
	doc: { type: Object },
	workflowStateField: { type: String, required: false },
	isTeamRequest: { type: Boolean, default: false },
})

const status = computed(() => (props.doc.docstatus ? "Submitted" : "Draft"))

const colorMap = {
	Draft: "gray",
	Submitted: "blue",
}
</script>
