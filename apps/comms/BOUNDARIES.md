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

**Channel-agnostic core.** The model knows a campaign has per-channel variants. It does not
know that email is special. Email is the first channel adapter, not the base case.

**Own tables, own prefix.** All tables `comms_*`. No columns added to GovKit tables, no
migrations touching another app. Comms data can be dumped and moved on its own.

**Settings and secrets namespaced.** `COMMS_*` only. SMTP credentials belong to comms, not
to GovKit's mail config.

## What comms owns

Campaigns, drafts, templates, per-group and per-channel content variants, send records,
opens and clicks, opt-out, and the schedule.

## What comms never owns

Who a person is, what org they belong to, cohort membership, tasks, meetings. Those have
homes already. Comms references them and keeps no private copy beyond a cached address and
a `(source, external_id)` handle.

## Spin-out test

Ask periodically: if we deleted GovKit tomorrow, what in comms breaks? The answer should be
`sources/govkit.py` and nothing else. When it is more than that, fix it then, not later.
