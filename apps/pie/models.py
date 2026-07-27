"""Pie has one model of its own: the link between an org and its lock-in vote.

Everything else the pie shows is computed from drops/orgs models — a read-model
(see apps/pie/services.py::compute_pie).
"""

from django.db import models


class PieLockVote(models.Model):
    """The lock-in decision for an org's starting split.

    "Nothing is final until lock-in by majority decision." The decision is a
    work-weighted vote (apps.votes): weight is the value each member has actually
    earned plus their starting balance — the same record the pie is built on. Money
    doesn't vote: a sponsor's stake is equity only and holds no membership, so it
    never appears in the electorate.

    One row per attempt. A vote that closes without a majority leaves the pie
    launched and adjustable; the team can start another whenever they are ready.
    ``locked`` marks the attempt that carried.
    """

    org = models.ForeignKey("orgs.Org", on_delete=models.CASCADE, related_name="pie_lock_votes")
    vote = models.OneToOneField("votes.Vote", on_delete=models.PROTECT, related_name="pie_lock")
    locked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        state = "locked" if self.locked else "open"
        return f"PieLockVote({self.org.slug}, {state})"
