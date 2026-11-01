# SPDX-License-Identifier: AGPL-3.0
#
# Ported from OpenViking (https://github.com/volcengine/OpenViking) @ commit
# 9a57854 — two semantic ports into this otherwise native module:
# - openviking/core/skill_loader.py (validate_skill_format): the frontmatter
#   validation rules and message style, re-expressed in _validate_frontmatter
#   against this module's line-based string parser (no code copied, no yaml
#   dependency); loading stays tolerant — issues are recorded, never fatal.
# - openviking/bot/vikingbot/agent/skills.py (_check_requirements, :182-191):
#   the requirements availability semantics (bins -> PATH lookup, env ->
#   environment lookup), rewritten in _missing_requirements (no code copied;
#   native frontmatter flow map instead of upstream's metadata JSON).
# Local changes: both ports keep upstream's facts/messages but are this
# repo's own implementation; per-field provenance notes live at each helper.
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import unicodedata
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from nexuscli.plugins import load_plugins


@dataclass(slots=True)
class Skill:
    name: str
    description: str
    path: Path
    content: str
    source: str = "project"
    version: str = ""
    tags: list[str] = field(default_factory=list)
    enabled: bool = True
    # Agent Skills spec frontmatter (allowed-tools / disable-model-invocation).
    # Both default to "absent": the spec fields are optional and a skill
    # without them behaves exactly like a native one.
    allowed_tools: list[str] = field(default_factory=list)
    disable_model_invocation: bool = False
    # L0/L1/L2 layered loading (spec 2026-11-02_add-skill-layered-loading):
    # ``abstract`` is the optional L0 one-liner shown in the candidate line in
    # place of the description; ``overview`` is the optional L1 condensation
    # returned by load_skill(level="overview"). Both default to "absent" and
    # every consumer falls back to the pre-layer behavior (candidate line uses
    # the description, load_skill level=full), so a SKILL.md without the new
    # fields behaves exactly as before. ``overview_generated`` marks an
    # overview that was auto-generated from the body rather than declared.
    abstract: str = ""
    overview: str = ""
    overview_generated: bool = False
    # Frontmatter lint issues recorded while parsing (rule codes and message
    # style adapted from OpenViking's skill_loader validation; see
    # _validate_frontmatter). Recording only — loading stays tolerant, a
    # skill with issues still loads exactly as it did before this field.
    validation_issues: list[str] = field(default_factory=list)
    # Optional availability requirements (spec 2026-11-02 §2.4, semantics
    # adapted from OpenViking vikingbot skills.py _check_requirements): a
    # declared bin must resolve on PATH, a declared env var must be set.
    # Skills with unmet requirements stay listed (/skill list marks them
    # unavailable with the missing items) but are excluded from the
    # automatic Top-K candidates; explicit loads still work.
    requirements_bins: list[str] = field(default_factory=list)
    requirements_env: list[str] = field(default_factory=list)
    missing_requirements: list[str] = field(default_factory=list)

    @property
    def body(self) -> str:
        return _strip_frontmatter(self.content).strip()

    @property
    def unavailable(self) -> bool:
        """True when a declared requirement (bin/env) is not met."""
        return bool(self.missing_requirements)


class SkillMatcher:
    """Rank enabled skills against a user request using lightweight lexical signals."""

    def __init__(self, skills: Iterable[Skill]):
        self.skills = tuple(skill for skill in skills if skill.enabled)

    def match(self, query: str, *, top_k: int = 5) -> list[Skill]:
        if top_k <= 0 or not query.strip():
            return []
        query_terms = _match_terms(query)
        ranked: list[tuple[int, str, Skill]] = []
        for skill in self.skills:
            score = self._score(query, query_terms, skill)
            if score > 0:
                ranked.append((score, skill.name, skill))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [skill for _score, _name, skill in ranked[:top_k]]

    @staticmethod
    def _score(query: str, query_terms: set[str], skill: Skill) -> int:
        score = 0
        if _explicit_skill_name(query, skill.name):
            score += 10_000 + len(_compact_match_text(skill.name))

        name_terms = _match_terms(skill.name)
        tag_terms = _match_terms(" ".join(skill.tags))
        description_terms = _match_terms(skill.description)
        score += 12 * len(query_terms & name_terms)
        score += 6 * len(query_terms & tag_terms)
        score += 2 * len(query_terms & description_terms)
        return score


class SkillContextBuffer:
    def __init__(self, limit: int = 3, usage_store: SkillStateStore | None = None):
        self.limit = limit
        self.usage_store = usage_store
        self._items: OrderedDict[str, str] = OrderedDict()

    def push(self, name: str | None, body: str | None) -> None:
        if not name or not body:
            return
        if name in self._items:
            del self._items[name]
        self._items[name] = body
        while len(self._items) > self.limit:
            self._items.popitem(last=False)
        self._record_use(name)

    def _record_use(self, name: str) -> None:
        """Count a real context injection; inert unless a store is attached.

        Opt-in by design: the same buffer also carries the non-skill
        session-goal reminder (context/goal.py), which must not be counted as
        skill usage, and a default-on recorder would make every unrelated
        push write the caller's real HOME. Wiring point for the model-invoked
        load_skill path: pass ``usage_store=SkillStateStore()`` where the
        buffer is constructed.
        """
        if self.usage_store is None:
            return
        with contextlib.suppress(OSError):
            self.usage_store.record_use(name)

    def drain(self) -> str:
        if not self._items:
            return ""
        chunks = [
            f"## Loaded Skill: {name}\n{body.strip()}"
            for name, body in self._items.items()
            if body.strip()
        ]
        self._items.clear()
        return "\n\n".join(chunks)

    def clear(self) -> None:
        self._items.clear()

    def is_empty(self) -> bool:
        return not self._items


class SkillStateStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or Path.home() / ".nexuscli" / "skills.json").expanduser()

    @property
    def usage_path(self) -> Path:
        """Usage counters live next to the state file (usage.json)."""
        return self.path.parent / "usage.json"

    def disabled(self) -> set[str]:
        if not self.path.exists():
            return set()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        values = data.get("disabled") if isinstance(data, dict) else None
        if not isinstance(values, list):
            return set()
        return {str(item) for item in values if str(item).strip()}

    def disable(self, name: str) -> None:
        values = self.disabled()
        values.add(name)
        self._write(values)

    def enable(self, name: str) -> None:
        values = self.disabled()
        values.discard(name)
        self._write(values)

    def usage(self) -> dict[str, dict[str, object]]:
        """Read ``{name: {used, last_used}}`` counters; empty dict when absent."""
        if not self.usage_path.exists():
            return {}
        try:
            data = json.loads(self.usage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def record_use(self, name: str) -> None:
        """Count one real injection of ``name``'s body into model context.

        Accumulates ``used`` and refreshes ``last_used`` in ``usage.json``
        (sibling of the state file). Atomic swap mirrors ``_write`` so a crash
        mid-write cannot corrupt the counters.
        """
        data = self.usage()
        entry = data.get(name)
        if not isinstance(entry, dict):
            entry = {}
        entry["used"] = int(entry.get("used", 0) or 0) + 1
        entry["last_used"] = _utc_timestamp()
        data[name] = entry
        self.usage_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.usage_path.with_name(self.usage_path.name + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.usage_path)

    def _write(self, disabled: set[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(
            json.dumps({"disabled": sorted(disabled)}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        # Atomic swap: a crash mid-write must not corrupt the state file, which
        # would silently re-enable every disabled skill.
        os.replace(tmp, self.path)


def _utc_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class SkillRegistry:
    """Load SKILL.md files from built-in, user, and project locations."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        builtin_root: str | Path | None = None,
        user_root: str | Path | None = None,
        state_store: SkillStateStore | None = None,
        user_agents_root: str | Path | None = None,
    ):
        self.project_root = Path(project_root).resolve()
        package_root = Path(__file__).resolve().parents[1]
        self.builtin_root = Path(builtin_root or package_root / "builtin_skills")
        self.user_root = Path(user_root or Path.home() / ".nexuscli" / "skills")
        # Agent Skills spec standard locations (pi / Claude Code compat):
        # ~/.agents/skills and <project>/.agents/skills are scanned in
        # addition to the native directories so ecosystem skill packs load
        # unchanged. Within each scope the native directory is scanned last,
        # so a native skill wins a name conflict with a standard-dir skill.
        self.user_agents_root = Path(user_agents_root or Path.home() / ".agents" / "skills")
        self.project_skill_root = self.project_root / ".nexuscli" / "skills"
        self.project_agents_skill_root = self.project_root / ".agents" / "skills"
        self.state_store = state_store or SkillStateStore()
        self._skills: dict[str, Skill] | None = None

    def reload(self) -> None:
        self._skills = None

    def list(self) -> list[Skill]:
        return self.enabled_skills()

    def all_skills(self) -> list[Skill]:
        skills = self._load_all()
        return [skills[name] for name in sorted(skills)]

    def enabled_skills(self) -> list[Skill]:
        return [skill for skill in self.all_skills() if skill.enabled]

    def load(self, name: str, *, include_disabled: bool = False) -> Skill | None:
        skill = self._load_all().get(name)
        if not skill:
            return None
        if not include_disabled and not skill.enabled:
            return None
        return skill

    def enable(self, name: str) -> bool:
        if not self.load(name, include_disabled=True):
            return False
        self.state_store.enable(name)
        self.reload()
        return True

    def disable(self, name: str) -> bool:
        if not self.load(name, include_disabled=True):
            return False
        self.state_store.disable(name)
        self.reload()
        return True

    def match(self, query: str, *, top_k: int = 5) -> list[Skill]:
        # disable-model-invocation skills are still loadable via load_skill /
        # /skill — they are just excluded from the automatic candidate list.
        # Same for skills with unmet requirements: they stay listed and
        # explicitly loadable, but a skill whose bin/env is missing must not
        # be offered to the model as a candidate (spec §2.4).
        candidates = [
            skill
            for skill in self.enabled_skills()
            if not skill.disable_model_invocation and not skill.unavailable
        ]
        return SkillMatcher(candidates).match(query, top_k=top_k)

    def create(
        self,
        name: str,
        *,
        description: str,
        body: str,
        scope: str = "project",
        version: str = "1.0.0",
        tags: list[str] | None = None,
        overwrite: bool = False,
    ) -> Skill:
        slug = _validate_skill_slug(name)
        description = description.strip()
        body = body.strip()
        if not description:
            raise ValueError("skill description must not be empty")
        if not body:
            raise ValueError("skill body must not be empty")

        path = self._skill_path(slug, scope)
        if path.exists() and not overwrite:
            raise FileExistsError(f'skill "{slug}" already exists in {scope} scope')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _render_skill_file(
                name=slug,
                description=description,
                body=body,
                version=version,
                tags=tags or [],
            ),
            encoding="utf-8",
        )
        self.reload()
        skill = self._load_skill_file(path, scope, self.state_store.disabled())
        if not skill:  # pragma: no cover - the file was just written successfully
            raise OSError(f"failed to load newly written skill: {path}")
        return skill

    def update(
        self,
        name: str,
        *,
        description: str | None = None,
        body: str | None = None,
        scope: str = "project",
        version: str | None = None,
        tags: list[str] | None = None,
    ) -> Skill:
        slug = _validate_skill_slug(name)
        path = self._skill_path(slug, scope)
        if not path.is_file():
            raise FileNotFoundError(f'skill "{slug}" does not exist in {scope} scope')
        existing = self._load_skill_file(path, scope, self.state_store.disabled())
        if not existing:
            raise ValueError(f'skill "{slug}" could not be parsed')
        return self.create(
            slug,
            description=description if description is not None else existing.description,
            body=body if body is not None else existing.body,
            scope=scope,
            version=version if version is not None else existing.version,
            tags=tags if tags is not None else existing.tags,
            overwrite=True,
        )

    def _scope_root(self, scope: str) -> Path:
        if scope == "project":
            root = self.project_skill_root.resolve()
            try:
                root.relative_to(self.project_root)
            except ValueError as exc:
                raise ValueError("project skill root must stay inside the project") from exc
            return root
        if scope == "user":
            return self.user_root.expanduser().resolve()
        raise ValueError('skill scope must be "project" or "user"')

    def _skill_path(self, name: str, scope: str) -> Path:
        root = self._scope_root(scope)
        path = root / name / "SKILL.md"
        try:
            path.resolve().relative_to(root)
        except ValueError as exc:
            raise ValueError("skill path must stay inside its scope root") from exc
        return path

    def _load_all(self) -> dict[str, Skill]:
        if self._skills is not None:
            return self._skills
        disabled = self.state_store.disabled()
        skills: dict[str, Skill] = {}
        for source, root in [
            ("builtin", self.builtin_root),
            ("user", self.user_agents_root),
            ("user", self.user_root),
            ("project", self.project_agents_skill_root),
            ("project", self.project_skill_root),
        ]:
            if not root.exists():
                continue
            for skill_file in sorted(root.glob("*/SKILL.md")):
                skill = self._load_skill_file(skill_file, source, disabled)
                if skill:
                    skills[skill.name] = skill
        # Plugin skills follow the existing layering: loaded with the fixed
        # "project" source (no new layer), so the regular project rules apply —
        # state_store.disable() and the enabled/enabled_skills flows work on
        # them unchanged. Scanning after the project root means a plugin skill
        # overrides a plain project skill on the same name ("last scan wins",
        # same chain as builtin < user < project above).
        for plugin in load_plugins(str(self.project_root)):
            if plugin.skills_dir is None:
                continue
            for skill_file in sorted(plugin.skills_dir.glob("*/SKILL.md")):
                skill = self._load_skill_file(skill_file, "project", disabled)
                if skill:
                    skills[skill.name] = skill
        self._skills = skills
        return skills

    def _load_skill_file(self, path: Path, source: str, disabled: set[str]) -> Skill | None:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            return None
        metadata = _parse_frontmatter(content)
        name = metadata.get("name") or path.parent.name
        description = metadata.get("description") or ""
        tags = _parse_tags(metadata.get("tags", ""))
        # Agent Skills spec fields, with underscore spellings accepted too.
        allowed_tools = _parse_allowed_tools(
            metadata.get("allowed-tools") or metadata.get("allowed_tools") or ""
        )
        disable_model_invocation = _parse_bool_flag(
            metadata.get("disable-model-invocation")
            or metadata.get("disable_model_invocation")
            or ""
        )
        # L0: the optional candidate-line abstract. Absent stays "" and every
        # consumer falls back to the description (candidate line) — the
        # backward-compat contract of the layered-loading spec.
        abstract = (metadata.get("abstract") or "").strip()
        # L1: an explicit frontmatter overview wins; otherwise one is
        # generated from the body's opening paragraph and marked as generated.
        declared_overview = (metadata.get("overview") or "").strip()
        body_text = _strip_frontmatter(content).strip()
        if declared_overview:
            overview, overview_generated = declared_overview, False
        else:
            overview = _generate_overview(body_text)
            overview_generated = bool(overview)
        requirements_bins, requirements_env = _parse_requirements(
            metadata.get("requirements") or ""
        )
        missing_requirements = _missing_requirements(requirements_bins, requirements_env)
        issues = _validate_frontmatter(metadata, dir_name=path.parent.name, body=body_text)
        return Skill(
            name=name,
            description=description,
            version=metadata.get("version") or "",
            tags=tags,
            source=source,
            path=path,
            content=content,
            enabled=name not in disabled,
            allowed_tools=allowed_tools,
            disable_model_invocation=disable_model_invocation,
            abstract=abstract,
            overview=overview,
            overview_generated=overview_generated,
            validation_issues=issues,
            requirements_bins=requirements_bins,
            requirements_env=requirements_env,
            missing_requirements=missing_requirements,
        )


def _parse_frontmatter(content: str) -> dict[str, str]:
    if not content.startswith("---"):
        return {}
    match = re.match(r"^---\s*\n(.*?)\n---\s*", content, re.S)
    if not match:
        return {}
    lines = match.group(1).splitlines()
    metadata: dict[str, str] = {}
    index = 0
    while index < len(lines):
        raw_line = lines[index]
        if ":" not in raw_line:
            index += 1
            continue
        key, value = raw_line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value == "|":
            index += 1
            block: list[str] = []
            while index < len(lines) and (lines[index].startswith(" ") or not lines[index].strip()):
                block.append(lines[index].strip())
                index += 1
            metadata[key] = " ".join(part for part in block if part)
            continue
        metadata[key] = value.strip().strip('"').strip("'")
        index += 1
    return metadata


def _strip_frontmatter(content: str) -> str:
    if not content.startswith("---"):
        return content
    return re.sub(r"^---\s*\n.*?\n---\s*", "", content, count=1, flags=re.S)


def _parse_tags(raw: str) -> list[str]:
    value = raw.strip()
    if not value:
        return []
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return [item.strip().strip('"').strip("'") for item in value.split(",") if item.strip()]


def _parse_allowed_tools(raw: str) -> list[str]:
    """Parse the Agent Skills ``allowed-tools`` frontmatter field.

    The spec spells it as a comma-separated string ("Bash(git:*), Read");
    an inline list ("[a, b]") is accepted as well since _parse_tags's
    bracket handling already covers that shape.
    """
    return _parse_tags(raw)


def _parse_bool_flag(raw: str) -> bool:
    return raw.strip().lower() in {"true", "1", "yes"}


# ---------------------------------------------------------------------------
# L1 auto-generation + frontmatter validation (layered loading, spec
# 2026-11-02_add-skill-layered-loading). The validation rules (required keys,
# name format/length/directory match, description length, body size, unknown
# keys tolerated) are adapted from OpenViking core/skill_loader.py
# (validate_skill_format, upstream commit 9a57854, AGPL-3.0) — rules and
# message style only, re-expressed against this module's line-based string
# parser instead of the upstream yaml-typed one, so no yaml dependency is
# introduced and loading stays tolerant (issues are recorded, never fatal).
# ---------------------------------------------------------------------------

_OVERVIEW_MAX_CHARS = 500
_OVERVIEW_SECTION_SPLIT = re.compile(r"^##\s", re.MULTILINE)
_SKILL_NAME_MAX_LENGTH = 64
_SKILL_DESCRIPTION_MAX_LENGTH = 1024
_SKILL_BODY_MAX_LINES = 500
# Upstream's name charset rule; deliberately wider than the strict lowercase
# slug used by create()/update() because ecosystem skill packs (the Agent
# Skills standard directories) legitimately carry capitalized names.
_SKILL_NAME_CHARS = re.compile(r"[A-Za-z0-9_-]+")


def _generate_overview(body: str) -> str:
    """Auto-generate an L1 overview from a skill body.

    Source: the body's first paragraph — within the opening block before the
    first ``##`` section heading when one exists — collapsed to one line and
    capped at ``_OVERVIEW_MAX_CHARS``. A leading ``#`` title line is skipped
    (it names the skill, it does not describe it); a body that opens directly
    with a ``##`` heading falls back to the first paragraph under that
    heading. Returns "" when the body yields no prose.
    """
    text = body.strip()
    if not text:
        return ""
    parts = _OVERVIEW_SECTION_SPLIT.split(text, maxsplit=1)
    head = re.sub(r"^#[^\n]*\n+", "", parts[0])
    tail = parts[1] if len(parts) > 1 else ""
    paragraph = _first_paragraph(head)
    if not paragraph and tail:
        tail = re.sub(r"^[^\n]*\n+", "", tail)  # drop the section heading line
        paragraph = _first_paragraph(tail)
    if len(paragraph) > _OVERVIEW_MAX_CHARS:
        paragraph = paragraph[: _OVERVIEW_MAX_CHARS - 3].rstrip() + "..."
    return paragraph


def _first_paragraph(text: str) -> str:
    """The first non-empty block of ``text``, blank-line separated, as one line."""
    for block in re.split(r"\n\s*\n", text.strip()):
        collapsed = " ".join(block.split())
        if collapsed:
            return collapsed
    return ""


def _validate_frontmatter(metadata: dict[str, str], *, dir_name: str, body: str) -> list[str]:
    """Record frontmatter lint issues as ``"rule: message"`` strings.

    Rules adapted from OpenViking's ``validate_skill_format``: required keys
    (name, description), name format (charset, 64-char cap, directory match),
    description 1024-char cap, and a 500-line body warning. Unknown
    frontmatter keys are tolerated with no issue — upstream's policy too, and
    the reason this module can parse hand-written ecosystem skills unchanged.
    Unlike upstream (which fails the load on required-key errors) this module
    keeps its historical tolerant fallbacks (name from the directory name,
    empty description) and only records the issues.
    """
    issues: list[str] = []
    name = (metadata.get("name") or "").strip()
    description = metadata.get("description") or ""
    if not name:
        issues.append("name_required: name is required")
    if not description.strip():
        issues.append("description_required: description is required")
    if name:
        if len(name) > _SKILL_NAME_MAX_LENGTH:
            issues.append("name_max_length: name must not exceed 64 characters")
        if not _SKILL_NAME_CHARS.fullmatch(name):
            issues.append(
                "name_allowed_characters: name may only contain letters, numbers, "
                "underscores, and hyphens"
            )
        if dir_name and name != dir_name:
            issues.append(
                f"name_matches_directory: name '{name}' does not match directory name '{dir_name}'"
            )
    if description.strip() and len(description) > _SKILL_DESCRIPTION_MAX_LENGTH:
        issues.append("description_max_length: description must not exceed 1024 characters")
    if len(body.splitlines()) > _SKILL_BODY_MAX_LINES:
        issues.append("body_max_lines: SKILL.md body exceeds 500 lines")
    return issues


# ---------------------------------------------------------------------------
# requirements availability filter (spec 2026-11-02 §2.4). Semantics adapted
# from OpenViking bot/vikingbot/agent/skills.py _check_requirements (lines
# 182-191 at upstream commit 9a57854; the file carries no SPDX header but is
# AGPL-3.0 licensed): a required bin must resolve on PATH, a required env var
# must be set. Rewritten for this module's line-based string parser — no code
# copied — so only the semantics are ported. Upstream keys its requirements
# under a vikingbot metadata JSON; here the field is a native frontmatter
# flow-style map: ``requirements: {bins: [git, rg], env: [GITHUB_TOKEN]}``.
# ---------------------------------------------------------------------------


def _parse_requirements(raw: str) -> tuple[list[str], list[str]]:
    """Parse the optional ``requirements: {bins: [...], env: [...]}`` map.

    Flow-style single line only (the line-based frontmatter parser's shape);
    the two list values reuse the tags list parsing (comma separated, quotes
    stripped). Unknown sub-keys are tolerated and ignored, matching the
    module's tolerant-unknowns policy.
    """
    text = raw.strip()
    if not text:
        return [], []
    if "{" in text and "}" in text:
        text = text[text.index("{") + 1 : text.rindex("}")]

    def _values(key: str) -> list[str]:
        # The lookbehind (not a word boundary: ``\b`` also matches after a
        # hyphen) keeps unknown sub-keys like ``missing-env``/``my-bins``
        # from being read as ``env``/``bins``.
        match = re.search(rf"(?<![\w-]){key}\s*:\s*\[([^\]]*)\]", text)
        if not match:
            return []
        return _parse_tags(match.group(1))

    return _values("bins"), _values("env")


def _missing_requirements(bins: list[str], env: list[str]) -> list[str]:
    """The unmet requirements as human-readable ``"bin: x"``/``"env: X"`` items.

    A bin is met when it resolves on PATH (``shutil.which``), an env var when
    it is set (non-empty lookup, same as upstream). Item format is stable
    contract for the /skill list unavailable marking.
    """
    missing: list[str] = []
    for bin_name in bins:
        if not shutil.which(bin_name):
            missing.append(f"bin: {bin_name}")
    for env_name in env:
        if not os.environ.get(env_name):
            missing.append(f"env: {env_name}")
    return missing


_SKILL_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$")
_ASCII_TERM = re.compile(r"[a-z0-9]+")
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_MATCH_STOPWORDS = {"and", "for", "from", "the", "this", "use", "with"}


def _validate_skill_slug(name: str) -> str:
    if not isinstance(name, str) or not _SKILL_SLUG.fullmatch(name):
        raise ValueError(
            "skill name must be a lowercase slug using letters, numbers, hyphens, or underscores"
        )
    return name


def _render_skill_file(
    *,
    name: str,
    description: str,
    body: str,
    version: str,
    tags: list[str],
) -> str:
    clean_tags = [str(tag).strip() for tag in tags if str(tag).strip()]
    lines = ["---", f"name: {name}", "description: |"]
    lines.extend(f"  {line}" for line in description.splitlines())
    lines.extend(
        [
            f"version: {json.dumps(str(version), ensure_ascii=False)}",
            f"tags: {json.dumps(clean_tags, ensure_ascii=False)}",
            "---",
            "",
            body.rstrip(),
            "",
        ]
    )
    return "\n".join(lines)


def _normalize_match_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^a-z0-9\u3400-\u4dbf\u4e00-\u9fff]+", " ", normalized)
    return " ".join(normalized.split())


def _compact_match_text(value: str) -> str:
    return _normalize_match_text(value).replace(" ", "")


def _explicit_skill_name(query: str, name: str) -> bool:
    normalized_query = _normalize_match_text(query)
    normalized_name = _normalize_match_text(name)
    if not normalized_name:
        return False
    tokens = normalized_name.split()
    pattern = r"(?<![a-z0-9])" + r"\s+".join(re.escape(token) for token in tokens)
    pattern += r"(?![a-z0-9])"
    return re.search(pattern, normalized_query) is not None


def _match_terms(value: str) -> set[str]:
    normalized = _normalize_match_text(value)
    terms = {
        term
        for term in _ASCII_TERM.findall(normalized)
        if len(term) >= 2 and term not in _MATCH_STOPWORDS
    }
    for run in _CJK_RUN.findall(normalized):
        if len(run) <= 4:
            terms.add(run)
        for size in (2, 3):
            terms.update(run[index : index + size] for index in range(len(run) - size + 1))
    return terms
