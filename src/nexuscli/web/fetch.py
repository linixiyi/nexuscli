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
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
        raise NetworkPolicyError("URL resolves to a private or local address")
