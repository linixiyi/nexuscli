"""Eval fixture (codebase-qa): a tiny deterministic token issuer.

Frozen eval fixture for the real-track suite (category ``codebase-qa``).
Scenario ``cqa-sh-fixture-token-issuer`` derives its ``final_contains``
anchor from this code, so the content is frozen: any edit here must
re-run the anchor derivation and update the scenario JSON in the same
change. Kept self-contained on purpose (stdlib only, no repo imports)
so the question tests code reading, not repo coupling.
"""

SCHEME_VERSION = "v3"
DEFAULT_EXPIRE_HOURS = 24


def _checksum(payload: str) -> str:
    # Deterministic pseudo-checksum: first two chars + payload length.
    return payload[:2] + str(len(payload))


def issue_token(subject: str, *, expire_hours: int = DEFAULT_EXPIRE_HOURS) -> str:
    payload = f"{subject}|{expire_hours}h"
    return f"tok-{SCHEME_VERSION}-{payload}-{_checksum(payload)}"
