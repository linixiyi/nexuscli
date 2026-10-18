"""Lane F (media) user-visible-behaviour tests — testing-matrix 14.1-14.5.

Complement the unit suites (test_image.py, test_file_read_pdf.py, test_lsp.py)
with the user-visible behaviour the matrix rows describe:

- 14.1 a local image pasted with ``@image:`` reaches the *request payload* of a
  vision model as a data URL, with real dimensions in the metadata.
- 14.2 a remote image URL is passed through untouched (no local fetch) and
  reaches the vision payload as-is.
- 14.3 a non-multimodal model (no vision marker in the model name) gets a
  readable plain-text degradation — the actual ``_build_payload`` request is a
  plain string, JSON-serializable, with no base64 blob leaking into it.
- 14.4 a generated two-page PDF is extracted page by page with the numbered
  line format the user sees.
- 14.5 writing a syntax-broken .py through the real ``write_file`` tool shows
  a Diagnostics block with the line number and the reason; a valid file gets
  none.
"""

from __future__ import annotations

import asyncio
import json

from PIL import Image

from nexuscli.config import load_config
from nexuscli.image import parse_image_references
from nexuscli.llm.openai_compatible import OpenAICompatibleClient
from nexuscli.tools import ToolRegistry, get_builtin_tools
from nexuscli.tools.base import ToolContext
from nexuscli.types import Message

# Real gateway model names (localhost gateway model list, verified 2026-10-18):
# a vision-marked one and a non-vision one.
VISION_MODEL = "cbai/glm-5v-turbo"  # name carries the "5v" marker → multimodal
PLAIN_MODEL = "cbai/glm-5.0"  # no vision marker → non-multimodal


def _vision_client() -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        provider_name="openai-compatible",
        model=VISION_MODEL,
        api_key="placeholder-key",
        base_url="https://api.example.test/v1",
    )


def _plain_client() -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        provider_name="openai-compatible",
        model=PLAIN_MODEL,
        api_key="placeholder-key",
        base_url="https://api.example.test/v1",
    )


def _make_png(path, size=(64, 32), color="red") -> None:
    Image.new("RGB", size, color).save(path)


def _payload_messages(client: OpenAICompatibleClient, content) -> list[dict]:
    payload = client._build_payload(
        [Message(role="user", content=content)], [], system_prompt="sys"
    )
    return payload["messages"]


# ---------------------------------------------------------------------------
# 14.1 local image → vision request payload
# ---------------------------------------------------------------------------


def test_local_image_reaches_vision_request_payload(tmp_path) -> None:
    _make_png(tmp_path / "shot.png", size=(64, 32))
    content = parse_image_references("看这张图 @image:shot.png", str(tmp_path))

    assert isinstance(content, list)
    image_part = content[1]
    assert image_part["type"] == "image_url"
    assert image_part["image_url"]["url"].startswith("data:image/")
    assert "base64," in image_part["image_url"]["url"]
    # Real, user-checkable dimensions: the thumbnail pass keeps 64x32 intact.
    assert image_part["metadata"] == {
        "source": str(tmp_path / "shot.png"),
        "width": 64,
        "height": 32,
    }

    # A vision model sends the data URL onward — the payload is serializable
    # and the image part travels intact.
    messages = _payload_messages(_vision_client(), content)
    serialized = json.dumps(messages)
    assert '"type": "image_url"' in serialized
    assert "base64," in serialized
    # The local file path never leaks into the request payload.
    assert str(tmp_path) not in serialized


# ---------------------------------------------------------------------------
# 14.2 remote image URL passthrough
# ---------------------------------------------------------------------------


def test_remote_image_url_passes_through_to_vision_payload() -> None:
    url = "https://example.com/photos/cat.jpg"
    content = parse_image_references(f"看 @image:{url} 好看吗", "/tmp")

    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "看 "}
    image_part = content[1]
    assert image_part["type"] == "image_url"
    # The URL is forwarded untouched — no local download, no metadata block.
    assert image_part["image_url"] == {"url": url}
    assert "metadata" not in image_part

    messages = _payload_messages(_vision_client(), content)
    assert messages[1]["content"][1] == {"type": "image_url", "image_url": {"url": url}}


# ---------------------------------------------------------------------------
# 14.3 non-multimodal degradation
# ---------------------------------------------------------------------------


def test_non_multimodal_model_degrades_to_readable_text_hint(tmp_path) -> None:
    _make_png(tmp_path / "shot.png", size=(64, 32))
    content = parse_image_references("看 @image:shot.png", str(tmp_path))
    client = _plain_client()
    assert not client.supports_images

    messages = _payload_messages(client, content)
    user_content = messages[1]["content"]

    # The request degrades to one plain string: readable hint, no crash, and
    # no base64 payload leaking to a model that cannot see it.
    assert isinstance(user_content, str)
    assert f"[Image omitted: {tmp_path / 'shot.png'}, 64x32]" in user_content
    assert "看" in user_content
    serialized = json.dumps(messages)
    assert "base64" not in serialized

    # A remote URL under the same non-multimodal model degrades the same way.
    remote = parse_image_references("看 @image:https://example.com/cat.jpg", str(tmp_path))
    remote_content = _payload_messages(client, remote)[1]["content"]
    assert isinstance(remote_content, str)
    assert "[Image omitted: remote image, ?x?]" in remote_content


# ---------------------------------------------------------------------------
# 14.4 PDF reading (two-page extraction)
# ---------------------------------------------------------------------------


def _one_page_pdf_bytes(text: str) -> bytes:
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


def _two_page_pdf(path, page_one: str, page_two: str) -> None:
    """Merge two handcrafted one-page PDFs into a real two-page file via pypdf
    (the optional dependency the read path itself uses)."""
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    for text in (page_one, page_two):
        reader = PdfWriter(clone_from=io.BytesIO(_one_page_pdf_bytes(text)))
        writer.add_page(reader.pages[0])
    with open(path, "wb") as handle:
        writer.write(handle)


def _registry_context(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    config = load_config(project_root=tmp_path)
    config.policy.hitl_mode = "never"
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    return registry.get("read_file"), ToolContext(cwd=str(tmp_path), config=config)


def test_read_pdf_extracts_both_pages(tmp_path, monkeypatch) -> None:
    _two_page_pdf(tmp_path / "doc.pdf", "ticket number A-1024", "approval code ZX-88")
    tool, context = _registry_context(tmp_path, monkeypatch)

    result = asyncio.run(tool.execute({"path": "doc.pdf"}, context))

    assert not result.is_error
    # Both pages contribute to the numbered extraction the user reads.
    assert "1: ticket number A-1024" in result.content
    assert "2: approval code ZX-88" in result.content
    assert result.display_summary == "Read doc.pdf"


# ---------------------------------------------------------------------------
# 14.5 Python syntax diagnostics via the real write_file tool
# ---------------------------------------------------------------------------


def _write_tool_context(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    config = load_config(project_root=tmp_path)
    config.policy.hitl_mode = "never"
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    return registry.get("write_file"), ToolContext(cwd=str(tmp_path), config=config)


def test_write_file_surfaces_syntax_diagnostic_line_and_reason(tmp_path, monkeypatch) -> None:
    tool, context = _write_tool_context(tmp_path, monkeypatch)

    broken = asyncio.run(
        tool.execute(
            {"path": "broken.py", "content": "def nope(:\n    pass\n"}, context
        )
    )
    assert not broken.is_error
    # The user-facing result names the file, then the diagnostic: line number
    # and reason, exactly what py_compile reports for the real file on disk.
    assert broken.content.startswith("Wrote broken.py")
    assert "Diagnostics:" in broken.content
    assert "line 1" in broken.content
    assert "SyntaxError" in broken.content
    assert (tmp_path / "broken.py").exists()  # the file itself was written

    # A valid file must not grow a Diagnostics block (clean write stays clean).
    valid = asyncio.run(
        tool.execute({"path": "fine.py", "content": "def ok():\n    return 1\n"}, context)
    )
    assert not valid.is_error
    assert "Diagnostics" not in valid.content
    assert valid.content == "Wrote fine.py"


def test_write_file_diagnostics_track_real_file_state(tmp_path, monkeypatch) -> None:
    """After fixing the same file the diagnostics disappear — the block always
    reflects the current on-disk state, not the first write."""
    tool, context = _write_tool_context(tmp_path, monkeypatch)
    asyncio.run(tool.execute({"path": "mod.py", "content": "x = (1,\n"}, context))
    fixed = asyncio.run(tool.execute({"path": "mod.py", "content": "x = (1,)\n"}, context))

    assert "Diagnostics" not in fixed.content
