"""
Comms tables. All `comms_*`, no foreign keys out (see BOUNDARIES.md): the org is
carried as its slug and resolved through `sources/govkit.py`.

Two tables, in the shape the approved mockup designed
(`demos.linkedtrust.us/comms-flows/v2/`, `week-data.js`):

  Edition  one week of the cohort. Holds the sections and the lines, once.
  Send     one audience's copy of that week: its subject, when it goes, its page.

One line is written once and appears in every email that needs it. `tpl` says
which audiences a line belongs in at all; `off` says which of them a person has
cut it from. That is why the same standup is not three rows, and why cutting it
from Mentors leaves Workers alone.
"""

from __future__ import annotations

import secrets

from django.db import models

# The emails. A slug, not a foreign key: another org can grow its own set
# without a migration, and nothing here assumes the accelerator.
#
# The first three are the cohort: who you are in the run decides which one you
# get. Supporters is not a role — it is a mailing list we build up, of people
# from the CRM and, later, from here. It gets news rather than things to do, so
# it does not share the cohort's sections.
WORKERS, VENTURES, MENTORS, SUPPORTERS = "w", "v", "m", "s"
COHORT_AUDIENCES = [WORKERS, VENTURES, MENTORS]
AUDIENCES = [
    (WORKERS, "Workers"),
    (VENTURES, "Ventures"),
    (MENTORS, "Mentors"),
    (SUPPORTERS, "Supporters"),
]
AUDIENCE_KEYS = [k for k, _ in AUDIENCES]

# Sections in the order they read. `cal` marks the one filled from the calendar,
# where a line carries a day and a time and points at Google Calendar. `carry`
# marks one that is the same every week — it is copied forward from last week's
# edition, so a standing footer is written once and then edited, never retyped.
# Titles are edited in place, so this is only the starting set.
DEFAULT_SECTIONS = [
    {"k": "goals", "title": "Goals this week", "tpl": [WORKERS, VENTURES]},
    # A mentor opens this to see when to turn up, and then who they would be
    # sitting with — so the week comes before the teams, and there is no
    # opportunities section for them at all (golda 2026-08-10).
    {"k": "cal", "title": "Coming up", "tpl": COHORT_AUDIENCES, "cal": True},
    {"k": "vent", "title": "Where the ventures are", "tpl": [MENTORS]},
    {"k": "news", "title": "What's new", "tpl": [SUPPORTERS]},
    {"k": "opp", "title": "New opportunities", "tpl": [WORKERS, VENTURES]},
    {"k": "foot", "title": "", "tpl": COHORT_AUDIENCES},
    # The standing footer on the supporters' email: follow us, and sponsor.
    {"k": "support", "title": "", "tpl": [SUPPORTERS], "carry": True},
]


class Edition(models.Model):
    """One week, and everything the cohort is being told about it."""

    org_slug = models.SlugField(max_length=64, db_index=True)
    week_number = models.PositiveIntegerField(null=True, blank=True)
    window_start = models.DateField()
    window_end = models.DateField()

    # [{k, title, tpl, cal}] — see DEFAULT_SECTIONS. Titles are editable.
    sections = models.JSONField(default=list, blank=True)
    # [{id, sec, uid, starts, day, time, title, note, flag, rec, href, tpl, off}]
    items = models.JSONField(default=list, blank=True)
    # The one snapshot an AI edit can be taken back to.
    previous = models.JSONField(null=True, blank=True)
    # The calendar's own timezone, so times read as the meeting was set.
    tz_name = models.CharField(max_length=64, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "comms_edition"
        ordering = ["-window_start"]
        constraints = [
            models.UniqueConstraint(
                fields=["org_slug", "window_start"], name="comms_one_edition_per_week"
            )
        ]

    def __str__(self) -> str:
        return f"{self.org_slug} week {self.week_number or self.window_start}"

    def section(self, key: str) -> dict | None:
        return next((s for s in self.sections if s.get("k") == key), None)

    def item(self, item_id: str) -> dict | None:
        return next((i for i in self.items if i.get("id") == item_id), None)

    def snapshot(self) -> dict:
        return {"sections": self.sections, "items": self.items}

    def restore(self, snapshot: dict) -> None:
        self.sections = snapshot.get("sections", [])
        self.items = snapshot.get("items", [])


class Send(models.Model):
    """One audience's copy of an edition: its subject, its date, its page.

    Scheduling is a date written on the thing itself, never a state a person has
    to remember to come back to. Nothing sends itself: a human presses the button
    (see abra `workersvc-comms-human-edit-required`), and sending also puts the
    week on its page.
    """

    edition = models.ForeignKey(Edition, on_delete=models.CASCADE, related_name="sends")
    audience = models.SlugField(max_length=40)

    subject = models.CharField(max_length=200, blank=True)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    # Putting the week on its page is its own act. Sending does it too, because
    # the email links there — but an email copied out and sent by hand still
    # needs a page to point at, and a link to paste in chat (golda 2026-08-10).
    published_at = models.DateTimeField(null=True, blank=True)
    # Who it actually went to, recorded at send time. Never re-derived.
    recipients = models.PositiveIntegerField(default=0)

    # Minted the first time it is published; kept afterwards so a link that has
    # been shared keeps working.
    public_token = models.CharField(max_length=32, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "comms_send"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["edition", "audience"], name="comms_one_send_per_audience"
            )
        ]

    def __str__(self) -> str:
        return f"{self.edition} → {self.audience}"

    @property
    def is_sent(self) -> bool:
        return self.sent_at is not None

    @property
    def is_published(self) -> bool:
        return self.published_at is not None

    def mint_token(self) -> str:
        if not self.public_token:
            self.public_token = secrets.token_urlsafe(16)[:32]
        return self.public_token


class Subscriber(models.Model):
    """One address on a list comms sends to, and where it came from.

    The cohort audiences need no rows here: who is a worker or a mentor is a
    fact GovKit and the doorway already hold, and comms asks them at send time.
    Supporters is the other kind of list — people who asked to hear from us,
    with no role in the run — so it is built up an import at a time.

    This is a handle and a cached address, never a second home for a person
    (apps/comms/BOUNDARIES.md). `source` and `external_id` say where the real
    record lives; re-importing updates the cached name and address from there.
    Unsubscribing is comms's own business and is keyed by email, so one
    unsubscribe covers every list and every source.
    """

    CRM = "crm"
    GOVKIT = "govkit"
    TYPED = "typed"

    org_slug = models.SlugField(max_length=64, db_index=True)
    audience = models.SlugField(max_length=40)

    source = models.CharField(max_length=16)
    external_id = models.CharField(max_length=64)
    email = models.EmailField()
    name = models.CharField(max_length=255, blank=True)
    # Which CRM tag brought them in, so a list can say what it is made of.
    via = models.CharField(max_length=120, blank=True)

    added_at = models.DateTimeField(auto_now_add=True)
    refreshed_at = models.DateTimeField(auto_now=True)
    unsubscribed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "comms_subscriber"
        ordering = ["name", "email"]
        constraints = [
            models.UniqueConstraint(
                fields=["org_slug", "audience", "source", "external_id"],
                name="comms_one_row_per_person_per_list",
            )
        ]
        indexes = [models.Index(fields=["org_slug", "audience", "email"])]

    def __str__(self) -> str:
        return f"{self.email} ({self.source})"
