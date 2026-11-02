"""Parse structured entries out of raw log text."""

from .reader import split_lines

# TODO: reject malformed timestamps instead of skipping the entry.


def parse_header(text):
    """Parse the leading ``# key=value`` line of a raw entry text."""
    for line in split_lines(text):
        if not line.startswith("# "):
            continue
        header = {}
        for item in line[2:].split(" "):
            key, _, value = item.partition("=")
            header[key] = value
        return header
    return {}


def parse_entry(text):
    """Parse one raw entry text into header dict, body lines and level."""
    lines = split_lines(text)
    header = parse_header(text)
    body = lines[1:] if header else lines
    level = header.get("level", "INFO")
    return {"header": header, "body": body, "level": level}
