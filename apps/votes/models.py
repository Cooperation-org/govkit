"""
Work-weighted votes (M2 — models now, views stubbed).

A Vote captures a weight snapshot at open time; Ballots record raw choices. Weighted
tallies are computed at read time from the snapshot, and raw ballots are always retained.
These are informal live meeting votes, NOT formal elections.
"""

from django.db import models

from apps.orgs.scoping import OrgScoped


class Vote(OrgScoped):
    question = models.CharField(max_length=500)
    # e.g. ["Yes", "No", "Abstain"] — free-form option list.
    options = models.JSONField(default=list)
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    # Per-membership weights captured when the vote opened, so a tally is reproducible
    # even as earnings change. Shape: {"<membership_id>": <weight>, ...}.
    weight_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-opened_at"]

    def __str__(self):
        return f"Vote({self.question[:40]})"

    @property
    def is_open(self):
        return self.closed_at is None


class Ballot(OrgScoped):
    vote = models.ForeignKey(Vote, on_delete=models.CASCADE, related_name="ballots")
    membership = models.ForeignKey(
        "orgs.Membership", on_delete=models.CASCADE, related_name="ballots"
    )
    choice = models.CharField(max_length=255)
    cast_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["vote", "membership"], name="uniq_ballot_vote_member"),
        ]
        ordering = ["vote", "membership"]

    def __str__(self):
        return f"Ballot({self.membership}: {self.choice})"
