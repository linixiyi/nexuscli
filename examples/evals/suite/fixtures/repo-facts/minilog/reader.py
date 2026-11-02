"""Read raw log text from disk or memory."""

# TODO: support gzip-compressed log files.


def read_log_file(path):
    """Return the full text of a log file."""
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def split_lines(text):
    """Split log text into non-empty stripped lines."""
    return [line.strip() for line in text.splitlines() if line.strip()]
