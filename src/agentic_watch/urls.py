"""URL normalisation and domain checks."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "yclid",
        "igshid",
        "_hsenc",
        "_hsmi",
        "ref",
        "cmpid",
    }
)


def normalise_url(url: str) -> str:
    """Lower-case scheme and host, drop fragments and tracking parameters, unify the trailing slash."""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    host = parts.hostname.lower() if parts.hostname else ""
    netloc = host
    if parts.port and not (
        (scheme == "https" and parts.port == 443) or (scheme == "http" and parts.port == 80)
    ):
        netloc = f"{host}:{parts.port}"
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    return urlunsplit((scheme, netloc, path, urlencode(sorted(query)), ""))


def host_of(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host.lower().removeprefix("www.")


def on_domains(url: str, domains: list[str]) -> bool:
    """True if the URL's host is one of the domains or a subdomain of one."""
    host = host_of(url)
    return any(host == d or host.endswith("." + d) for d in domains)
