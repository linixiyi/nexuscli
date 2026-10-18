from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style

from nexuscli.llm.model_profiles import ModelProfile
from nexuscli.llm.openai_compatible import ModelListingProfile

ModelTab = Literal["default", "custom", "fetch"]
ActionKind = Literal["select", "add", "delete", "add_fetched", "refetch"]


@dataclass(frozen=True, slots=True)
class ModelSelectorAction:
    kind: ActionKind
    profile: ModelProfile | None = None
    fetched: tuple[ModelListingProfile, ...] = ()


@dataclass(slots=True)
class ModelSelectorState:
    defaults: list[ModelProfile]
    custom: list[ModelProfile]
    current_provider: str
    current_model: str
    tab: ModelTab = "default"
    indexes: dict[ModelTab, int] = field(
        default_factory=lambda: {"default": 0, "custom": 0, "fetch": 0}
    )
    # Fetch tab: gateway listing results and interaction state. ``fetch`` is
    # the unfiltered listing; ``fetch_query`` filters it case-insensitively;
    # ``fetch_checked`` stores *unfiltered* indices so toggling the search
    # box never desynchronises the selection.
    fetch: list[ModelListingProfile] = field(default_factory=list)
    fetch_base_url: str = ""
    fetch_query: str = ""
    fetch_checked: set[int] = field(default_factory=set)
    fetch_error: str = ""

    def __post_init__(self) -> None:
        for tab in ("default", "custom"):
            profiles = self.defaults if tab == "default" else self.custom
            for index, profile in enumerate(profiles):
                if (
                    profile.provider == self.current_provider.lower()
                    and profile.model == self.current_model
                ):
                    self.tab = tab
                    self.indexes[tab] = index
                    break

    @property
    def item_count(self) -> int:
        if self.tab == "default":
            return len(self.defaults)
        if self.tab == "custom":
            return len(self.custom) + 1
        return self.filtered_fetch_count + 1

    @property
    def filtered_fetch(self) -> list[tuple[int, ModelListingProfile]]:
        query = self.fetch_query.strip().lower()
        return [
            (index, profile)
            for index, profile in enumerate(self.fetch)
            if not query or query in profile.model.lower()
        ]

    @property
    def filtered_fetch_count(self) -> int:
        return len(self.filtered_fetch)

    @property
    def index(self) -> int:
        return self.indexes[self.tab]

    def switch_tab(self, delta: int = 1) -> None:
        tabs: tuple[ModelTab, ...] = ("default", "custom", "fetch")
        self.tab = tabs[(tabs.index(self.tab) + delta) % len(tabs)]
        self.indexes[self.tab] = min(self.indexes[self.tab], self.item_count - 1)

    def move(self, delta: int) -> None:
        self.indexes[self.tab] = (self.index + delta) % max(self.item_count, 1)

    def selected_fetched(self) -> list[ModelListingProfile]:
        """Profiles the user asked to add: checked rows, or the cursor row when none checked."""
        if self.tab != "fetch":
            return []
        rows = self.filtered_fetch
        if self.fetch_checked:
            return [profile for index, profile in rows if index in self.fetch_checked]
        if rows and self.index < len(rows):
            return [rows[self.index][1]]
        return []

    def toggle_fetch_check(self) -> None:
        rows = self.filtered_fetch
        if self.tab == "fetch" and rows and self.index < len(rows):
            index = rows[self.index][0]
            if index in self.fetch_checked:
                self.fetch_checked.discard(index)
            else:
                self.fetch_checked.add(index)

    def check_all_fetched(self, checked: bool) -> None:
        if self.tab != "fetch":
            return
        if checked:
            self.fetch_checked.update(index for index, _ in self.filtered_fetch)
        else:
            self.fetch_checked.clear()

    def selected_action(self) -> ModelSelectorAction:
        if self.tab == "custom" and self.index == len(self.custom):
            return ModelSelectorAction("add")
        if self.tab == "fetch":
            selected = self.selected_fetched()
            if selected:
                return ModelSelectorAction("add_fetched", fetched=tuple(selected))
            return ModelSelectorAction("select")
        profiles = self.defaults if self.tab == "default" else self.custom
        return ModelSelectorAction("select", profiles[self.index])

    def delete_action(self) -> ModelSelectorAction | None:
        if self.tab != "custom" or self.index >= len(self.custom):
            return None
        return ModelSelectorAction("delete", self.custom[self.index])

    def render(self) -> StyleAndTextTuples:
        fragments: StyleAndTextTuples = [("class:command", "> /model\n\n")]
        fragments.extend([("class:title", "Model  ·  ")])
        fragments.extend(self._tab("default", f"Default ({len(self.defaults)})"))
        fragments.append(("", "   "))
        fragments.extend(self._tab("custom", f"Custom ({len(self.custom)})"))
        fragments.append(("", "   "))
        fragments.extend(self._tab("fetch", f"API 拉取 ({len(self.fetch)})"))
        fragments.extend(
            [
                ("", "\n"),
                ("class:line", "─" * 78 + "\n"),
                ("class:heading", "Current\n"),
                ("class:muted", "  Provider : "),
                ("", self.current_provider + "\n"),
                ("class:muted", "  Model    : "),
                ("", self.current_model + "\n\n"),
            ]
        )
        profiles = self.defaults if self.tab == "default" else self.custom
        if self.tab == "fetch":
            fragments.extend(self._render_fetch())
        else:
            if not profiles and self.tab == "custom":
                fragments.append(("class:muted", "  No custom models yet.\n\n"))
            for index, profile in enumerate(profiles):
                selected = index == self.index
                current = (
                    profile.provider == self.current_provider.lower()
                    and profile.model == self.current_model
                )
                marker = "> " if selected else "  "
                style = "class:selected" if selected else "class:model"
                check = " ✓" if current else ""
                fragments.append((style, f"{marker}{profile.name}{check}\n"))
                fragments.append(
                    (
                        "class:muted",
                        f"    {profile.provider} · modelID: {profile.model} · "
                        f"context: {profile.context_window:,}\n",
                    )
                )
                if profile.description:
                    fragments.append(("class:muted", f"    {profile.description}\n"))
        if self.tab == "custom":
            selected = self.index == len(self.custom)
            marker = "> " if selected else "  "
            style = "class:selected" if selected else "class:model"
            fragments.extend(
                [
                    (style, f"{marker}[+] Add custom model...\n"),
                    ("class:muted", "    Add a new Bring Your Own Key (BYOK) model\n"),
                ]
            )
        fragments.extend(
            [
                ("class:footer", "\nTab/←→ switch · ↑↓ navigate · Enter select"),
                (
                    "class:footer",
                    " · d delete · Esc back" if self.tab == "custom" else " · Esc back",
                ),
            ]
        )
        if self.tab == "fetch":
            fragments.append(
                ("class:footer", " · space 勾选 · a 全选 · n 取消全选 · Enter 添加所选")
            )
        return fragments

    def _render_fetch(self) -> StyleAndTextTuples:
        fragments: StyleAndTextTuples = []
        if self.fetch_error:
            fragments.append(("class:error", f"  拉取失败: {self.fetch_error}\n"))
        if not self.fetch:
            fragments.append(("class:muted", "  尚未拉取。回到选择器后会提示输入 API 地址。\n"))
            fragments.append(("class:muted", "  按 r 重新输入地址并拉取；Esc 退出。\n\n"))
            return fragments
        base_label = self.fetch_base_url or "(unknown)"
        fragments.append(("class:muted", f"  来源: {base_label}\n"))
        query_label = self.fetch_query or "(全部)"
        fragments.append(("class:muted", f"  搜索: {query_label}\n\n"))
        for row, (original_index, profile) in enumerate(self.filtered_fetch):
            selected = row == self.index
            checked = original_index in self.fetch_checked
            marker = "> " if selected else "  "
            style = "class:selected" if selected else "class:model"
            checkbox = "[x]" if checked else "[ ]"
            context = f" · ctx {profile.context_window:,}" if profile.context_window else ""
            badges = _capability_badges(profile)
            fragments.append((style, f"{marker}{checkbox} {profile.model}{context}{badges}\n"))
        summary = f"\n  共 {len(self.fetch)} 个，已勾选 {len(self.fetch_checked)} 个\n"
        fragments.append(("class:muted", summary))
        return fragments

    def _tab(self, tab: ModelTab, label: str) -> StyleAndTextTuples:
        return [("class:tab.active" if self.tab == tab else "class:tab", f" {label} ")]


def _capability_badges(profile: ModelListingProfile) -> str:
    """Short suffix badges for discovered reasoning-effort capability.

    Tri-state discipline: only declared capabilities get a badge; ``None``
    (gateway did not say) renders nothing rather than a guess.
    """
    badges = ""
    if profile.reasoning_effort_supported:
        badges += " · 强度✓"
    if profile.thinking_can_disable:
        badges += " · 思考可关"
    return badges


async def run_model_selector(state: ModelSelectorState) -> ModelSelectorAction | None:
    bindings = KeyBindings()
    control = FormattedTextControl(text=state.render, focusable=True, show_cursor=False)

    def refresh(event) -> None:
        event.app.invalidate()

    @bindings.add("tab")
    @bindings.add("right")
    def _next_tab(event) -> None:
        state.switch_tab(1)
        refresh(event)

    @bindings.add("s-tab")
    @bindings.add("left")
    def _previous_tab(event) -> None:
        state.switch_tab(-1)
        refresh(event)

    @bindings.add("up")
    def _up(event) -> None:
        state.move(-1)
        refresh(event)

    @bindings.add("down")
    def _down(event) -> None:
        state.move(1)
        refresh(event)

    @bindings.add("enter")
    def _select(event) -> None:
        event.app.exit(result=state.selected_action())

    @bindings.add("d")
    def _delete(event) -> None:
        action = state.delete_action()
        if action:
            event.app.exit(result=action)

    # Fetch-tab keys: toggling, select-all/clear, search typing, refetch.
    @bindings.add("space")
    def _toggle_check(event) -> None:
        state.toggle_fetch_check()
        refresh(event)

    @bindings.add("a")
    def _check_all(event) -> None:
        state.check_all_fetched(True)
        refresh(event)

    @bindings.add("n")
    def _check_none(event) -> None:
        state.check_all_fetched(False)
        refresh(event)

    @bindings.add("r")
    def _refetch(event) -> None:
        # Modelled as an exit with a marker action so the blocking URL prompt
        # and the network call stay outside prompt_toolkit's event loop.
        event.app.exit(result=ModelSelectorAction("refetch"))

    @bindings.add("backspace")
    def _search_backspace(event) -> None:
        state.fetch_query = state.fetch_query[:-1]
        count = state.filtered_fetch_count
        state.indexes["fetch"] = min(state.index, count - 1) if count else 0
        refresh(event)

    @bindings.add("<any>")
    def _search_type(event) -> None:
        # Specific bindings (tab/up/down/space/a/n/r/...) win over <any> in
        # prompt_toolkit's precedence rules; whatever reaches here is typed
        # search text.
        text = event.data if isinstance(event.data, str) else ""
        if text and text.isprintable():
            state.fetch_query += text
            state.indexes["fetch"] = 0
            refresh(event)

    @bindings.add("escape")
    @bindings.add("c-c")
    def _cancel(event) -> None:
        event.app.exit(result=None)

    application: Application[ModelSelectorAction | None] = Application(
        layout=Layout(Window(control, wrap_lines=False, always_hide_cursor=True)),
        key_bindings=bindings,
        style=Style.from_dict(
            {
                # Foreground-only palette drawn from the terminal's own ANSI
                # slots, so contrast is guaranteed on light and dark themes.
                # Literal greys measured 3.9–4.4:1 against a light terminal
                # and would vanish; ansibrightblack is the theme's own muted
                # tone and the terminal keeps it legible either way.
                "command": "ansimagenta",
                "title": "bold",
                "heading": "bold",
                "line": "ansibrightblack",
                "tab": "ansibrightblack",
                "tab.active": "bold ansigreen",
                "model": "",
                "selected": "bold ansigreen",
                "muted": "ansibrightblack",
                "footer": "italic ansibrightblack",
                "error": "bold ansired",
            }
        ),
        full_screen=False,
        mouse_support=False,
    )
    return await application.run_async()
