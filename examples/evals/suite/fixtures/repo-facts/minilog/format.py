"""Render log statistics as fixed-width text tables."""

from .stats import count_levels, top_messages

# TODO: add a CSV renderer next to the fixed-width table.


def format_count(number):
    """Zero-pad a count to a width of four digits."""
    return f"{int(number):04d}"


def render_table(text):
    """Render a fixed-width per-level table plus the most frequent line."""
    counts = count_levels(text)
    leaders = top_messages(text, limit=1)
    rows = ["LEVEL     COUNT"]
    for level in ("ERROR", "WARN", "INFO"):
        rows.append(f"{level:<9} {format_count(counts.get(level, 0))}")
    rows.append(f"leader    {leaders[0] if leaders else '-'}")
    return "\n".join(rows)
