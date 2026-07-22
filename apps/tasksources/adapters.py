"""
Task-source adapters — fetch eligible tasks from an external tracker via its REST API.

Design goals
------------
* A small ABC (`TaskSourceAdapter.fetch_tasks() -> list[TaskDTO]`) so GitHub Issues /
  Linear can be added later by writing another concrete adapter.
* The concrete `TaigaAdapter` talks to Taiga's **REST API** with an auth token
  (`TaskSourceConfig.api_token`, encrypted at rest) — NEVER its database. The legacy
  pipeline hit the Taiga DB directly (`GovernanceToken/earning/earnings.py`); that is the
  antipattern being replaced, because self-hosters have no DB access.
* The adapter normalizes tracker records into `TaskDTO`s. Valuation (turning tags/hours
  into governance value) lives in `services.py`, so the adapter stays a thin, testable
  boundary and both valuation modes share one fetch path.

HTTP uses the standard library (`urllib`) so the toolkit adds no third-party HTTP
dependency; tests mock `urllib.request.urlopen`.

Assumed Taiga REST surface (the real base URL + token are pending — see scratch.md Q3):

  GET /api/v1/projects/by_slug?slug=<slug>            -> {"id": ...}          (slug -> id)
  GET /api/v1/userstory-statuses?project=<id>         -> [{"id","slug",...}]  (status id->slug)
  GET /api/v1/userstory-custom-attributes?project=<id>-> [{"id","name",...}]  (attr name->id)
  GET /api/v1/userstories?project=<id>&page=<n>       -> [ <userstory>, ... ]  (paginated)
  GET /api/v1/userstories/custom-attributes-values/<us_id> -> {"attributes_values": {...}}

Auth header defaults to ``Authorization: Bearer <token>`` (Taiga normal auth tokens);
application tokens use the ``Application`` scheme — see ``AUTH_SCHEME`` note below.
"""

from __future__ import annotations

import abc
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

# hours_field values that mean "use Taiga's native story points" rather than a custom
# attribute. Anything else in hours_field is treated as a custom-attribute name.
NATIVE_POINTS_FIELDS = {"points", "total_points"}

# Taiga auth scheme. "Bearer" for normal/session auth tokens; "Application" for
# application tokens. Kept here (not hardcoded per-call) so it is easy to switch.
AUTH_SCHEME = "Bearer"


@dataclass
class TaskDTO:
    """A tracker task normalized into the fields GovKit valuation cares about.

    Raw valuation inputs only — the sync service decides, per the org's ValuationConfig,
    which of these become the persisted value/hours/cash on a TrackedTask.
    """

    external_id: str
    subject: str = ""
    status_slug: str = ""
    external_url: str = ""
    tags: list[str] = field(default_factory=list)
    assignee_username: Optional[str] = None
    assignee_user_id: Optional[int] = None
    # Resolved numeric inputs for hours_rate mode (None when not configured/available).
    hours: Optional[Decimal] = None
    cash: Optional[Decimal] = None


@dataclass
class OpenTaskDTO:
    """An OPEN (not-closed) tracker task, normalized for the read-only open-work view.

    Separate from TaskDTO on purpose: this feeds the cohort dash's "tasks to do" card
    and carries deep-link fields (story ref, project slug) instead of valuation inputs.
    It never enters the TrackedTask valuation pipeline.
    """

    external_id: str
    subject: str = ""
    status: str = ""  # human-readable status name (e.g. "In progress")
    external_url: str = ""
    assignee_label: Optional[str] = None  # stable tracker username, never a display name
    ref: Optional[int] = None  # tracker story ref (Taiga's #NN), for board deep links
    project_slug: Optional[str] = None  # tracker project slug, for board deep links


class TaskSourceAdapter(abc.ABC):
    """Base class for tracker adapters (Taiga first; GitHub Issues / Linear can follow)."""

    def __init__(self, config):
        self.config = config  # a tasksources.TaskSourceConfig

    @abc.abstractmethod
    def fetch_tasks(self) -> list[TaskDTO]:
        """Return eligible (done/archived/historical) tasks as normalized TaskDTOs.

        Eligibility uses ``config.done_statuses``. The adapter does not touch the DB and
        does not persist anything — the sync service upserts TrackedTask rows.
        """
        raise NotImplementedError

    def fetch_open_tasks(self) -> list[OpenTaskDTO]:
        """Return the tracker's OPEN (not-closed) tasks as OpenTaskDTOs.

        Optional capability: adapters that cannot report open work raise
        NotImplementedError. Read-only — nothing is persisted.
        """
        raise NotImplementedError(f"{type(self).__name__} does not report open tasks")


class TaigaAdapter(TaskSourceAdapter):
    """Fetch done user stories from Taiga over its REST API."""

    #: default page size Taiga returns; used to decide whether to request another page.
    _DEFAULT_PAGE_SIZE = 30

    # -- HTTP plumbing -----------------------------------------------------------------

    def _headers(self) -> dict:
        headers = {"Accept": "application/json"}
        token = self.config.api_token
        if token:
            headers["Authorization"] = f"{AUTH_SCHEME} {token}"
        return headers

    def _url(self, path: str, params: Optional[dict] = None) -> str:
        base = self.config.base_url.rstrip("/")
        url = f"{base}/{path.lstrip('/')}"
        if params:
            # Drop None values so callers can pass optional params inline.
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url = f"{url}?{urllib.parse.urlencode(clean)}"
        return url

    def _get(self, path: str, params: Optional[dict] = None):
        """GET a path and return ``(parsed_json, response_headers)``.

        Isolated so tests can mock ``urllib.request.urlopen`` (the true HTTP boundary).
        """
        url = self._url(path, params)
        request = urllib.request.Request(url, headers=self._headers(), method="GET")
        # timeout: a stalled tracker must never tie up a gunicorn worker for 30s
        # (the dash tasks card calls this on page load).
        with urllib.request.urlopen(
            request, timeout=10
        ) as response:  # nosec B310 - config-supplied base_url
            payload = response.read().decode("utf-8")
            resp_headers = {k.lower(): v for k, v in response.headers.items()}
        data = json.loads(payload) if payload else None
        return data, resp_headers

    # -- Taiga concept resolution ------------------------------------------------------

    def _project_ids(self) -> list[str]:
        """Resolve ``project_selector`` (comma-separated ids and/or slugs) to project ids."""
        selector = (self.config.project_selector or "").strip()
        if not selector:
            return []
        ids: list[str] = []
        for token in (t.strip() for t in selector.split(",")):
            if not token:
                continue
            if token.isdigit():
                ids.append(token)
            else:
                data, _ = self._get("/api/v1/projects/by_slug", {"slug": token})
                if data and data.get("id") is not None:
                    ids.append(str(data["id"]))
        return ids

    def _status_slugs(self, project_id: str) -> dict:
        """Map ``userstory status id -> slug`` for a project (stories carry only the id)."""
        return {sid: row.get("slug", "") for sid, row in self._statuses(project_id).items()}

    def _statuses(self, project_id: str) -> dict:
        """Map ``userstory status id -> full status row`` (slug, name, is_closed, ...)."""
        data, _ = self._get("/api/v1/userstory-statuses", {"project": project_id})
        return {str(row["id"]): row for row in (data or [])}

    def _custom_attr_ids(self, project_id: str) -> dict:
        """Map ``custom-attribute name (lowercased) -> id`` for a project."""
        data, _ = self._get("/api/v1/userstory-custom-attributes", {"project": project_id})
        return {row.get("name", "").lower(): str(row["id"]) for row in (data or [])}

    def _custom_attr_values(self, us_id) -> dict:
        """Fetch ``{attr_id: value}`` for one story, or ``{}`` if none/not found."""
        try:
            data, _ = self._get(f"/api/v1/userstories/custom-attributes-values/{us_id}")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {}
            raise
        if not data:
            return {}
        return {str(k): v for k, v in (data.get("attributes_values") or {}).items()}

    def _iter_stories(self, project_id: str) -> Iterable[dict]:
        """Yield every user story for a project, following Taiga page pagination."""
        page = 1
        while True:
            data, headers = self._get("/api/v1/userstories", {"project": project_id, "page": page})
            rows = data or []
            for row in rows:
                yield row
            has_next = headers.get("x-pagination-next")
            if has_next:
                # Explicit next-page URL present -> keep going.
                page += 1
                continue
            # No pagination headers: stop when a short/empty page is returned.
            if len(rows) < self._DEFAULT_PAGE_SIZE:
                break
            page += 1

    # -- normalization -----------------------------------------------------------------

    @staticmethod
    def _normalize_tags(raw_tags) -> list[str]:
        """Taiga tags come as ``[[name, color], ...]`` or plain strings; return names."""
        tags: list[str] = []
        for tag in raw_tags or []:
            if isinstance(tag, (list, tuple)):
                if tag and tag[0]:
                    tags.append(str(tag[0]))
            elif tag:
                tags.append(str(tag))
        return tags

    @staticmethod
    def _to_decimal(value) -> Optional[Decimal]:
        if value is None or value == "":
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None

    def _resolve_hours(self, story: dict, attr_ids: dict) -> Optional[Decimal]:
        field_name = (self.config.hours_field or "").strip()
        if not field_name or field_name.lower() in NATIVE_POINTS_FIELDS:
            return self._to_decimal(story.get("total_points"))
        return self._resolve_custom(story, attr_ids, field_name)

    def _resolve_cash(self, story: dict, attr_ids: dict) -> Optional[Decimal]:
        field_name = (self.config.cash_field or "").strip()
        if not field_name:
            return None
        return self._resolve_custom(story, attr_ids, field_name)

    def _resolve_custom(self, story, attr_ids, field_name) -> Optional[Decimal]:
        attr_id = attr_ids.get(field_name.lower())
        if not attr_id:
            return None
        values = self._custom_attr_values(story["id"])
        return self._to_decimal(values.get(attr_id))

    def _needs_custom_attrs(self) -> bool:
        hours = (self.config.hours_field or "").strip().lower()
        cash = (self.config.cash_field or "").strip()
        return bool(cash) or (bool(hours) and hours not in NATIVE_POINTS_FIELDS)

    # -- public API --------------------------------------------------------------------

    def fetch_tasks(self) -> list[TaskDTO]:
        eligible = {s.lower() for s in (self.config.done_statuses or [])}
        want_attrs = self._needs_custom_attrs()
        results: list[TaskDTO] = []
        for project_id in self._project_ids():
            status_slugs = self._status_slugs(project_id)
            attr_ids = self._custom_attr_ids(project_id) if want_attrs else {}
            for story in self._iter_stories(project_id):
                slug = status_slugs.get(str(story.get("status")), "")
                if slug.lower() not in eligible:
                    continue
                extra = story.get("assigned_to_extra_info") or {}
                results.append(
                    TaskDTO(
                        external_id=str(story["id"]),
                        subject=story.get("subject", "") or "",
                        status_slug=slug,
                        external_url=story.get("permalink", "") or "",
                        tags=self._normalize_tags(story.get("tags")),
                        assignee_username=extra.get("username"),
                        assignee_user_id=story.get("assigned_to"),
                        hours=self._resolve_hours(story, attr_ids),
                        cash=self._resolve_cash(story, attr_ids),
                    )
                )
        return results

    def fetch_open_tasks(self) -> list[OpenTaskDTO]:
        """Open work: every story whose status is not closed (``is_closed`` false).

        Uses the status row's ``is_closed`` flag (not ``done_statuses``, which describes
        valuation eligibility) so "open" matches what the tracker's board shows. A story
        whose status row is unknown is treated as open — never hide work by accident.
        """
        results: list[OpenTaskDTO] = []
        for project_id in self._project_ids():
            statuses = self._statuses(project_id)
            for story in self._iter_stories(project_id):
                status_row = statuses.get(str(story.get("status"))) or {}
                if status_row.get("is_closed", False):
                    continue
                extra = story.get("assigned_to_extra_info") or {}
                project_extra = story.get("project_extra_info") or {}
                ref = story.get("ref")
                results.append(
                    OpenTaskDTO(
                        external_id=str(story["id"]),
                        subject=story.get("subject", "") or "",
                        status=status_row.get("name") or status_row.get("slug", "") or "",
                        external_url=story.get("permalink", "") or "",
                        assignee_label=extra.get("username") or None,
                        ref=ref if isinstance(ref, int) else None,
                        project_slug=project_extra.get("slug") or None,
                    )
                )
        return results


# adapter_type value -> adapter class. Add GitHub/Linear here as they are built.
_ADAPTERS = {
    "taiga": TaigaAdapter,
}


def get_adapter(config) -> TaskSourceAdapter:
    """Factory: return the adapter for a config's ``adapter_type``."""
    try:
        adapter_cls = _ADAPTERS[config.adapter_type]
    except KeyError as exc:
        raise ValueError(f"No adapter for adapter_type={config.adapter_type!r}") from exc
    return adapter_cls(config)
