"""Tests for PDF text extraction in read_file (optional pypdf dependency)."""

from __future__ import annotations

import asyncio
import sys

from nexuscli.config import load_config
from nexuscli.tools import ToolRegistry, get_builtin_tools
from nexuscli.tools.base import ToolContext


def _minimal_pdf(text: str) -> bytes:
    """Build a one-page PDF whose content stream draws *text* with /F1."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size "
        + str(len(objects) + 1).encode()
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(xref_pos).encode()
        + b"\n%%EOF\n"
    )
    return bytes(out)


def _read_tool_and_context(tmp_path, monkeypatch):
    """Wire up the read_file tool exactly like tests/test_tools.py:11-33."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = load_config(project_root=tmp_path)
    config.policy.hitl_mode = "never"
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    tool = registry.get("read_file")
    assert tool
    context = ToolContext(cwd=str(tmp_path), config=config)
    return tool, context


def test_read_file_extracts_pdf_text(tmp_path, monkeypatch):
    (tmp_path / "doc.pdf").write_bytes(_minimal_pdf("hello nexus pdf"))
    tool, context = _read_tool_and_context(tmp_path, monkeypatch)

    result = asyncio.run(tool.execute({"path": "doc.pdf"}, context))

    assert not result.is_error
    assert "hello nexus pdf" in result.content
    assert "1: hello nexus pdf" in result.content  # numbered like the text path
    assert result.display_summary and "doc.pdf" in result.display_summary


def test_read_pdf_missing_dependency_error(tmp_path, monkeypatch):
    (tmp_path / "doc.pdf").write_bytes(_minimal_pdf("hello nexus pdf"))
    (tmp_path / "note.txt").write_text("plain text", encoding="utf-8")
    tool, context = _read_tool_and_context(tmp_path, monkeypatch)
    # Make the lazy `import pypdf` raise ImportError without uninstalling it.
    monkeypatch.setitem(sys.modules, "pypdf", None)

    result = asyncio.run(tool.execute({"path": "doc.pdf"}, context))
    # Regression anchor: plain text reads are unaffected in the same round.
    txt = asyncio.run(tool.execute({"path": "note.txt"}, context))

    assert result.is_error
    assert "pypdf" in result.content
    assert "--extra pdf" in result.content
    assert not txt.is_error
    assert "plain text" in txt.content


def test_read_pdf_error_for_corrupt_file(tmp_path, monkeypatch):
    (tmp_path / "bad.pdf").write_bytes(b"this is definitely not a pdf")
    tool, context = _read_tool_and_context(tmp_path, monkeypatch)

    result = asyncio.run(tool.execute({"path": "bad.pdf"}, context))

    assert result.is_error
    assert "Failed to extract PDF text" in result.content
