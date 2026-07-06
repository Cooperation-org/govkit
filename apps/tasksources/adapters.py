"""
Task-source adapter interface — STUB for the taiga agent.

The taiga agent implements a TaigaAdapter that talks to Taiga's REST API (NOT its DB),
supports both valuation modes (direct-value tags and hours x rate), and upserts
TrackedTask rows keyed on (org, source, external_id). This module defines the seam only.
"""

from __future__ import annotations

import abc


class TaskSourceAdapter(abc.ABC):
    """Base class for tracker adapters (Taiga first; GitHub/Linear can follow)."""

    def __init__(self, config):
        self.config = config  # a tasksources.TaskSourceConfig

    @abc.abstractmethod
    def sync(self) -> int:
        """Fetch eligible tasks and upsert TrackedTask rows. Returns count synced."""
        raise NotImplementedError


def get_adapter(config) -> TaskSourceAdapter:
    """Factory: return the adapter for a config's adapter_type. Taiga agent fills this in."""
    raise NotImplementedError("Taiga agent: implement adapter selection + TaigaAdapter.")
