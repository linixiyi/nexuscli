from __future__ import annotations

import html
import ipaddress
import re
import socket
from urllib.parse import urlparse

import httpx


class NetworkPolicyError(ValueError):
    pass


async def fetch_url(url: str, max_length: int = 10_000, timeout: float = 15.0) -> str:
    _validate_public_url(url)
    # Redirects are followed manually so every hop re-passes the public-URL check;
    # httpx's automatic following would bypass it after the first validation.
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        current_url = url
        response: httpx.Response | None = None
        for _ in range(5):
            response = await client.get(
                current_url, headers={"user-agent": "NexusCLI-Python/0.1.0"}
            )
            if response.is_redirect and response.next_request is not None:
                current_url = str(response.next_request.url)
                _validate_public_url(current_url)
                continue
            break
        assert response is not None
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        text = response.text
        if "html" in content_type:
            text = extract_text_from_html(text)
        if len(text) > max_length:
            text = text[:max_length] + "\n... [truncated]"
        return text or "(empty page)"


def extract_text_from_html(raw_html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", raw_html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise NetworkPolicyError("only http/https URLs are allowed")
    if not parsed.hostname:
        raise NetworkPolicyError("URL must include a hostname")
    host = parsed.hostname
    try:
        ip = ipaddress.ip_address(host)
        _reject_private_ip(ip)
        return
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise NetworkPolicyError(f"cannot resolve host: {host}") from exc
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue  # unparseable resolver answer — ignore this record
        # Not caught above on purpose: NetworkPolicyError must propagate,
        # otherwise every private address would silently pass validation.
        _reject_private_ip(ip)


def _reject_private_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    """Refuse loopback / link-local / LAN / metadata addresses.

    ``ipaddress.is_private`` also covers 198.18.0.0/15, the benchmarking range
    that TUN-style transparent proxies (Clash, Surge, sing-box) hand out as
    fake-IP. Treating that as "private" made every public hostname fail with
    "resolves to a private or local address" on machines running such a proxy,
    which took web_fetch offline entirely while curl on the same box returned
    200 (measured: example.com -> 198.18.0.38, is_private True). The classes
    below are the ones an SSRF guard actually cares about; the fake-IP range
    is normal internet traffic wearing a proxy mask.
    """
    if (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or _is_private_or_shared(ip)
    ):
        raise NetworkPolicyError("URL resolves to a private or local address")


_FAKE_IP_RANGES = (ipaddress.ip_network("198.18.0.0/15"),)  # RFC 2544 benchmarking


def _is_private_or_shared(ip) -> bool:
    """True for RFC1918 / ULA / CGNAT, minus the fake-IP range proxies use."""
    if any(ip in network for network in _FAKE_IP_RANGES if ip.version == network.version):
        return False
    return ip.is_private
