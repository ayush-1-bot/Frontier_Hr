<template>
	<ion-page>
		<ion-content :fullscreen="true">
			<div v-if="balance.data" class="mx-4 mt-4 rounded bg-gray-100 p-3 text-sm text-gray-700">
				{{ balanceText }}
			</div>
			<FormView
				v-if="formFields.data"
				:key="props.id || 'new'"
				doctype="Short Leave Request"
				v-model="request"
				:isSubmittable="true"
				:fields="fields"
				:id="props.id"
				@validateForm="validateForm"
			/>
		</ion-content>
	</ion-page>
</template>

<script setup>
import { IonPage, IonContent } from "@ionic/vue"
import { createResource } from "frappe-ui"
import dayjs from "dayjs"
import { computed, inject, ref, watch } from "vue"
import FormView from "@/components/FormView.vue"

const props = defineProps({ id: String })
const employee = inject("$employee")
const __ = inject("$translate")
const request = ref({})

// Most short leaves are asked for on the day itself.
if (!props.id) request.value.date = dayjs().format("YYYY-MM-DD")

const formFields = createResource({
	url: "hrms.api.get_doctype_fields",
	params: { doctype: "Short Leave Request" },
	auto: true,
})

// Read-only or derived on the server: shown once saved, never asked for.
const DERIVED = ["naming_series", "hours", "units_consumed", "policy", "amended_from"]
const OWNER = ["employee", "employee_name", "department", "company"]

const fields = computed(() =>
	(formFields.data || [])
		.filter((field) => {
			if (DERIVED.includes(field.fieldname)) return false
			// On a new request these are filled in from the session, so there
			// is nothing to ask and nothing the employee may change.
			return !(!props.id && OWNER.includes(field.fieldname))
		})
		.map((field) => {
			field = { ...field }
			if (OWNER.includes(field.fieldname)) field.read_only = true
			return field
		})
)

const balance = createResource({
	url: "frontier_hr.short_leave.api.get_balance",
	makeParams: () => ({ employee: employee.data?.name, date: request.value.date }),
})

const balanceText = computed(() => {
	const b = balance.data
	if (!b) return ""
	return __("{0} of {1} short leaves left, {2} hours, until {3}", [
		b.remaining,
		b.allowed,
		b.remaining_hours,
		b.period_end,
	])
})

watch(
	() => request.value.date,
	(date) => {
		if (date && employee.data?.name) balance.reload()
	},
	{ immediate: true }
)

function validateForm() {
	if (!props.id) {
		request.value.employee = employee.data.name
		request.value.company = employee.data.company
	}
}
</script>
