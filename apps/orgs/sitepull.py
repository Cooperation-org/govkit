"""
Pull a draft join-page profile from the team's own website.

Why this exists: a team asked to write a pitch from a blank textarea writes
nothing, and a team that writes it under time pressure writes marketing voice.
Their site already says what they do, in their words. So we read it once and
hand it back as a DRAFT they edit. Nothing here is ever saved on its own —
`fetch_profile` returns a dict, the setup screen shows it in editable fields,
and the admin presses save. We never generate a sentence; every string returned
by this module came off the team's own page.

Only three things come back that the team did not literally type: the choice of
WHICH sentence (og:description, then meta description, then the first real
paragraph), and the two image URLs. Those are still their words and their images.

Safety: the URL comes from a user, so this is an SSRF surface. Every hop is
checked — scheme must be http/https, the resolved address must be public, and
redirects are followed by hand so a public host cannot bounce us onto 169.254
or 10.x. Response size and time are capped. Stdlib only, on purpose: the cohort
VM installs nothing new to deploy this.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from html import unescape
from html.parser import HTMLParser
from urllib.error import URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

TIMEOUT = 8
MAX_BYTES = 1_000_000
MAX_REDIRECTS = 4
USER_AGENT = "GovKit join-page setup (+https://workers.vc)"

# Hosts we recognise as a team's social presence, label to show for each.
SOCIAL_HOSTS = {
    "linkedin.com": "LinkedIn",
    "x.com": "X",
    "twitter.com": "X",
    "github.com": "GitHub",
    "youtube.com": "YouTube",
    "instagram.com": "Instagram",
    "facebook.com": "Facebook",
    "bsky.app": "Bluesky",
    "mastodon.social": "Mastodon",
    "discord.gg": "Discord",
    "substack.com": "Substack",
}


class SiteUnreachable(Exception):
    """We could not read the site. The message is shown to the admin verbatim."""


def _is_public_address(host: str) -> bool:
    """True only if every address this host resolves to is publicly routable."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return True


def _check(url: str) -> str:
    """Reject anything that is not a public http(s) URL. Returns the URL."""
    parts = urlparse(url)
    if parts.scheme not in ("http", "https"):
        raise SiteUnreachable("That needs to be an http:// or https:// address.")
    if not parts.hostname:
        raise SiteUnreachable("That address has no site name in it.")
    if not _is_public_address(parts.hostname):
        raise SiteUnreachable("That address is not a public website.")
    return url


def _read(url: str) -> tuple[str, str]:
    """GET the page, following redirects by hand. Returns (html, final_url)."""
    seen = 0
    current = _check(url)
    while True:
        request = Request(
            current,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
            method="GET",
        )
        try:
            # Redirects are handled here, not by the opener, so each hop is checked.
            with _no_redirect_opener().open(request, timeout=TIMEOUT) as response:
                status = response.status
                if status in (301, 302, 303, 307, 308):
                    seen += 1
                    if seen > MAX_REDIRECTS:
                        raise SiteUnreachable("That site redirects too many times.")
                    location = response.headers.get("Location", "")
                    if not location:
                        raise SiteUnreachable("That site sent us in a circle.")
                    current = _check(urljoin(current, location))
                    continue
                if status != 200:
                    raise SiteUnreachable(f"The site answered {status}.")
                ctype = response.headers.get("Content-Type", "")
                if "html" not in ctype and ctype:
                    raise SiteUnreachable("That address is not a web page.")
                raw = response.read(MAX_BYTES)
        except SiteUnreachable:
            raise
        except URLError as exc:
            raise SiteUnreachable("We could not reach that site.") from exc
        except (OSError, ValueError) as exc:
            raise SiteUnreachable("We could not read that site.") from exc
        charset = "utf-8"
        match = re.search(r"charset=([\w-]+)", ctype or "", re.I)
        if match:
            charset = match.group(1)
        return raw.decode(charset, errors="replace"), current


def _no_redirect_opener():
    """An opener that hands 3xx back to us instead of following it itself."""
    from urllib.request import HTTPRedirectHandler, build_opener

    class _Keep(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    return build_opener(_Keep)


class _PageParser(HTMLParser):
    """Collects the handful of things a join page needs off an HTML document."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.title = ""
        self.icon = ""
        self.h1 = ""
        self.paragraphs = []
        self.links = []
        self._capture = None
        self._buffer = []

    def handle_starttag(self, tag, attrs):
        attributes = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (attributes.get("property") or attributes.get("name") or "").lower()
            content = attributes.get("content", "").strip()
            if key and content and key not in self.meta:
                self.meta[key] = content
        elif tag == "link":
            rel = attributes.get("rel", "").lower()
            href = attributes.get("href", "").strip()
            if href and "icon" in rel and not self.icon:
                self.icon = href
        elif tag == "a":
            href = attributes.get("href", "").strip()
            if href:
                self.links.append(href)
        elif tag in ("title", "h1", "p") and self._capture is None:
            self._capture = tag
            self._buffer = []

    def handle_endtag(self, tag):
        if tag != self._capture:
            return
        text = re.sub(r"\s+", " ", "".join(self._buffer)).strip()
        if tag == "title" and not self.title:
            self.title = text
        elif tag == "h1" and not self.h1:
            self.h1 = text
        elif tag == "p" and text:
            self.paragraphs.append(text)
        self._capture = None
        self._buffer = []

    def handle_data(self, data):
        if self._capture is not None:
            self._buffer.append(data)


def _clean(value: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", unescape(value or "")).strip()
    return value[:limit].strip()


def _name_from(title: str, meta: dict) -> str:
    """The team's name: og:site_name, else the title up to its first separator."""
    if meta.get("og:site_name"):
        return _clean(meta["og:site_name"], 255)
    # "IntegralMass | Risk Intelligence Platform" -> "IntegralMass"
    return _clean(re.split(r"\s+[|–—·:]\s+", title or "")[0], 255)


def _tagline_from(title: str, meta: dict, parser) -> str:
    """One line saying what this is. Their words, whichever place they wrote them."""
    if meta.get("og:title") and meta["og:title"] != title:
        candidate = _clean(meta["og:title"], 255)
        if candidate:
            return candidate
    parts = re.split(r"\s+[|–—·:]\s+", title or "", maxsplit=1)
    if len(parts) == 2 and parts[1].strip():
        return _clean(parts[1], 255)
    if parser.h1:
        return _clean(parser.h1, 255)
    return ""


def _pitch_from(meta: dict, parser) -> str:
    """What they are building. The description they wrote, else their first paragraph."""
    for key in ("og:description", "description", "twitter:description"):
        if meta.get(key):
            return _clean(meta[key], 2000)
    for paragraph in parser.paragraphs:
        if len(paragraph) >= 80:
            return _clean(paragraph, 2000)
    return ""


def _socials_from(links: list, base: str) -> list:
    """Social profiles the site links out to, one per host, in page order."""
    out, seen = [], set()
    own_host = (urlparse(base).hostname or "").lower().removeprefix("www.")
    for href in links:
        url = urljoin(base, href)
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        if not host or host == own_host:
            continue
        for known, label in SOCIAL_HOSTS.items():
            if host == known or host.endswith("." + known):
                if label in seen:
                    break
                # A bare share button links to the network's own front page.
                if len(urlparse(url).path.strip("/")) < 2:
                    break
                seen.add(label)
                out.append({"label": label, "url": url})
                break
    return out


def fetch_profile(url: str) -> dict:
    """Read the team's site and return a draft profile for them to edit.

    Every value is a string off their page or "". Raises SiteUnreachable with a
    message meant to be shown to the admin as-is.
    """
    if not (url or "").strip():
        raise SiteUnreachable("Put your website address in first.")
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    html, final_url = _read(url)

    parser = _PageParser()
    try:
        parser.feed(html)
    except Exception:
        # A broken document still gave us whatever parsed before it broke.
        pass

    meta = parser.meta
    cover = meta.get("og:image") or meta.get("twitter:image") or ""
    logo = meta.get("og:logo") or parser.icon or ""
    return {
        "website": final_url,
        "display_name": _name_from(parser.title, meta),
        "tagline": _tagline_from(parser.title, meta, parser),
        "pitch": _pitch_from(meta, parser),
        "cover_image_url": urljoin(final_url, cover) if cover else "",
        "logo_url": urljoin(final_url, logo) if logo else "",
        "socials": _socials_from(parser.links, final_url),
    }
