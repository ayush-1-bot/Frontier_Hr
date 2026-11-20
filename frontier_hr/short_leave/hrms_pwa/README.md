# Short Leave in the Frappe HR mobile app

The mobile app at `/hrms` is a compiled Vue bundle that ships inside **hrms**,
not a Frappe page, so a doctype in another app cannot add a tile to it. The
Quick Links list is a literal array in `hrms/frontend/src/views/Home.vue`.

Adding "Request Short Leave" therefore means editing hrms and rebuilding its
bundle. Those edits live in the hrms repo, so **`bench update` overwrites
them**. They are kept here so they can be put back in one step.

## Re-applying after an hrms update

```bash
cd apps/hrms
PWA=../frontier_hr/frontier_hr/short_leave/hrms_pwa
cp $PWA/ShortLeaveRequestForm.vue  frontend/src/views/leave/
cp $PWA/ShortLeaveRequestItem.vue  frontend/src/components/
cp $PWA/shortLeave.js              frontend/src/data/
git apply $PWA/short_leave_pwa.patch
yarn build-pwa
```

If `git apply` fails because hrms moved those lines, make the two edits by
hand — they are small:

- `frontend/src/router/leaves.js` — two routes, `ShortLeaveRequestFormView`
  (`/short-leave-requests/new`) and `ShortLeaveRequestDetailView`
  (`/short-leave-requests/:id`), both rendering `ShortLeaveRequestForm.vue`.
- `frontend/src/views/Home.vue` — one more entry in `quickLinks`, pointing at
  `ShortLeaveRequestFormView`.
- `frontend/src/components/FormField.vue` — a `Time` branch rendering a
  **native** `<input type="time">`. hrms never implemented the Time fieldtype:
  it left a `<!-- Time -->` comment with nothing under it, so a Time field drew
  its label and no input at all. It has to be a native input, not frappe-ui's
  `Input`: that component validates its `type` prop against a whitelist with
  no "time" in it, renders nothing, and only warns on the console. Drop this
  hunk if a later hrms implements the fieldtype itself.

- `frontend/src/components/RequestPanel.vue` and `RequestList.vue`, plus
  `frontend/src/data/config/requestSummaryFields.js` — put short leave in the
  Home screen's **My Requests** list. That list is a hardcoded set of four
  doctypes with no extension point, so the resource, the row component and the
  tap-through field list all have to be registered by hand. There is no Team
  Requests feed: `get_requests` answers for the caller alone unless they are HR.

Then refresh the patch for next time:

```bash
cd apps/hrms
git diff -- frontend/src/router/leaves.js frontend/src/views/Home.vue \
    frontend/src/components/FormField.vue frontend/src/components/RequestList.vue \
    frontend/src/components/RequestPanel.vue \
    frontend/src/data/config/requestSummaryFields.js \
  > ../frontier_hr/frontier_hr/short_leave/hrms_pwa/short_leave_pwa.patch
```

## What the form does

It renders through hrms's own generic `FormView` against
`hrms.api.get_doctype_fields("Short Leave Request")`, so the fields follow the
doctype and nothing here needs changing when a field is added.

Employee, name, department and company are filled in from the session and
hidden on a new request; hours, units, policy and the series are derived on
the server and only shown once saved. The banner above the form reads the
quota from `frontier_hr.short_leave.api.get_balance`, which refuses to answer
for anybody but the caller unless they are HR.

Built against hrms 16.53.1.
