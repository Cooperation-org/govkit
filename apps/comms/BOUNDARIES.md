# apps/comms — separation of concerns

**This app is not part of GovKit.** It lives here because GovKit already holds the people,
the orgs, and a dashboard we control. If it turns out to be a good tool on its own, it gets
spun out into its own service. Everything below exists to keep that exit cheap.

[SRC: Golda, 2026-08-08 — "make sure you put in its directory some kind of separation of
concerns to the idea that if it is a really useful tool, we might spin it out separately from
GovKit because it's not really part of GovKit."]

## The contract

**One direction only.** `apps/comms` may read GovKit. No GovKit app may import from
`apps.comms`. If GovKit needs something from comms, comms publishes it — signal, API, or a
row comms owns. Grep before you wire: an import pointing out of comms is the thing that
welds it in place.

**No foreign keys out of comms.** An audience member is stored as `(source, external_id)`,
never `ForeignKey(orgs.Org)` or `ForeignKey(accounts.User)`. Resolution goes through one
adapter module. On spin-out, that adapter becomes an HTTP client and nothing else changes.

**One adapter file names every GovKit fact comms uses.** `apps/comms/sources/govkit.py`.
Who is in a group, what their address is, what org they belong to, what is on the calendar.
Nothing else in comms touches a GovKit model directly. The list of things in that file is the
exact API a spun-out service would need.

**Org-scoped from row one.** Every campaign, template, audience, and send record carries an
org. There is no implicit "the current cohort". workers.vc is one org among many; a venture
using this for its own people must be the same code path, not a variant.

**Channel-agnostic core.** A send is one audience's copy of an edition. Nothing in the
model says that copy has to be an email. Email is the first channel, not the base case.

**Own tables, own prefix.** All tables `comms_*`. No columns added to GovKit tables, no
migrations touching another app. Comms data can be dumped and moved on its own.

**Settings and secrets namespaced.** `COMMS_*` only. SMTP credentials belong to comms, not
to GovKit's mail config.

## What comms owns

Editions (one week of the cohort: its sections and its lines) and sends (one audience's
copy of an edition: its subject, its date, whether it went, and the page it published).
Also, when they arrive: opt-out, opens and clicks.

The shape follows the approved mockup (`demos.linkedtrust.us/comms-flows/v2/`,
`week-data.js`). One line is written once and carries `tpl` (which audiences it belongs
in at all) and `off` (which of them a person cut it from), so cutting the standup from
Mentors leaves Workers alone without copying the line three times.

## What comms never owns

Who a person is, what org they belong to, cohort membership, tasks, meetings. Those have
homes already. Comms references them and keeps no private copy beyond a cached address and
a `(source, external_id)` handle.

## Spin-out test

Ask periodically: if we deleted GovKit tomorrow, what in comms breaks? The answer should be
`sources/govkit.py` and nothing else. When it is more than that, fix it then, not later.
