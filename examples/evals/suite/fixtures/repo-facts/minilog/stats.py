"""Aggregate statistics over parsed log entries."""

from .parser import parse_entry

# TODO: cache per-level counts across repeated scans.
# TODO: add a percentile helper for entry sizes.


def count_levels(text):
    """Count entries per log level (INFO / WARN / ERROR) in a raw text."""
    counts = {"INFO": 0, "WARN": 0, "ERROR": 0}
    for line in text.splitlines():
        if not line.strip():
            continue
        entry = parse_entry(line)
        level = entry["level"]
        counts[level] = counts.get(level, 0) + 1
    return counts


def top_messages(text, limit=3):
    """Return the ``limit`` most frequent non-header lines of a raw text."""
    seen = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        seen[stripped] = seen.get(stripped, 0) + 1
    ranked = sorted(seen.items(), key=lambda item: item[1], reverse=True)
    return [message for message, _ in ranked[:limit]]
