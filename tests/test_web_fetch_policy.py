"""SSRF guard behaviour for the web-fetch tool.

The guard used ipaddress.is_private as a single test, which also covers
198.18.0.0/15 — the RFC 2544 benchmarking range that TUN-style proxies
(Clash, Surge, sing-box) hand out as fake-IP. On such a machine every public
hostname resolved into that range and web_fetch refused all of them, while
curl on the same box returned 200. The classes the guard actually needs are
pinned here, together with the fake-IP exception.
"""

from __future__ import annotations

import ipaddress

import pytest

from nexuscli.web.fetch import NetworkPolicyError, _reject_private_ip


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",  # loopback
        "::1",
        "10.0.0.5",  # RFC1918
        "172.16.3.4",
        "192.168.1.10",
        "169.254.169.254",  # cloud metadata
        "fd00::1",  # ULA
        "224.0.0.1",  # multicast
        "0.0.0.0",  # unspecified
    ],
)
def test_internal_addresses_are_refused(address: str) -> None:
    with pytest.raises(NetworkPolicyError):
        _reject_private_ip(ipaddress.ip_address(address))


@pytest.mark.parametrize(
    "address",
    [
        "198.18.0.38",  # fake-IP handed out by a transparent proxy
        "198.19.255.1",  # top of the same /15
        "93.184.216.34",  # example.com's real address
        "140.82.121.4",  # github.com
    ],
)
def test_public_and_fake_ip_addresses_are_allowed(address: str) -> None:
    # A proxy's fake-IP range is ordinary internet traffic wearing a mask;
    # rejecting it takes the whole web tool offline on the developer's machine.
    _reject_private_ip(ipaddress.ip_address(address))
