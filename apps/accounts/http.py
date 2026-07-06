"""
Minimal JSON-over-HTTP helpers built on the standard library.

The OIDC/OAuth flows only need two operations — POST a form and GET JSON — so we avoid
adding `requests` as a dependency. Both helpers raise `HttpError` on a non-2xx response
and return the parsed JSON body otherwise. Tests mock `urlopen` (or the helpers directly)
rather than hitting the network.
"""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_TIMEOUT = 15


class HttpError(Exception):
    """Non-2xx HTTP response (or transport failure)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _read_json(request: Request) -> dict:
    try:
        with urlopen(request, timeout=DEFAULT_TIMEOUT) as resp:  # nosec B310 - fixed hosts
            body = resp.read().decode("utf-8")
    except HTTPError as exc:  # 4xx/5xx — urllib raises rather than returns
        raise HttpError(f"HTTP {exc.code}", status=exc.code) from exc
    except URLError as exc:  # DNS/connection failure
        raise HttpError(f"Request failed: {exc.reason}") from exc
    return json.loads(body)


def post_form(url: str, data: dict, headers: dict | None = None) -> dict:
    """POST url-encoded form data, expecting a JSON response."""
    payload = urlencode({k: v for k, v in data.items() if v is not None}).encode("utf-8")
    req = Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    return _read_json(req)


def get_json(url: str, headers: dict | None = None) -> dict:
    """GET a JSON response."""
    req = Request(url, method="GET")
    req.add_header("Accept", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    return _read_json(req)
