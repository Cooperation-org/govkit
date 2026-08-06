# Template decisions

NEVER PUT COMMENTS IN TEMPLATES. NOT ONE. NOT IN ANY TEMPLATE.
No `{# #}`, no `<!-- -->`, no `/* */` inside a `<style>` block, no `//` inside
an inline `<script>`. A multi-line `{# ... #}` is not stripped by Django at
all, and the other three are not comments to a browser at all — every one of
them renders into the page source for anyone who views it. Comments in
templates read as junk. The reasoning that used to sit in them lives here.

## `base.html`

- Copy-to-clipboard for any `.gk-copy` button, on every page. Text buttons say "Copied"; icon-only buttons show a tick. It lived in two page templates and was missing from a third, so a Copy button silently did nothing there. `members.html` relies on this and defines none of its own.

## `accounts/profile.html`

- Nothing to save until the upload has actually landed, so the save button stays off rather than failing after a click.

## `commons/pool.html`

- Whole-card link, the stretched-link pattern: the anchor on the name throws a transparent overlay across the card, so a click anywhere goes to the person. Their own links sit above that overlay and still go where they say, which nesting one anchor inside another could not do.
- Skills are the first thing under the name, because they are what a venture came here to read.

## `orgs/invite_door.html`

- Golda's words, 2026-08-05. This cohort's dates are written out on purpose — one run. A second cohort should read `Cohort.starts_on` / `Cohort.ends_on`.

## `orgs/mentors.html`

- One card per mentor, in the house language: warm paper, hairline, ink. A face, their own words, and the one thing you came to do.
