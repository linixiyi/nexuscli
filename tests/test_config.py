from __future__ import annotations

import json

from nexuscli.config import FeatureConfig, load_config


def test_config_precedence(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    (home / ".nexuscli").mkdir(parents=True)
    (project / ".nexuscli").mkdir(parents=True)
    (home / ".nexuscli" / "config.json").write_text(
        json.dumps({"llm": {"provider": "home", "model": "home-model"}}),
        encoding="utf-8",
    )
    (project / ".nexuscli" / "config.json").write_text(
        json.dumps({"llm": {"provider": "project", "model": "project-model"}}),
        encoding="utf-8",
    )
    (project / ".env").write_text("NEXUSCLI_MODEL=env-file-model\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("NEXUSCLI_PROVIDER", "process")

    config = load_config(
        project_root=project,
        overrides={"llm": {"model": "cli-model"}},
    )

    assert config.llm.provider == "process"
    assert config.llm.model == "cli-model"


def test_provider_specific_api_key(tmp_path, monkeypatch):
    # Patch both HOME and USERPROFILE: Path.home() prefers USERPROFILE on
    # Windows, so an unpatched USERPROFILE would leak the developer's real
    # ~/.nexuscli/config.json into this test (win32 gotcha).
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("NEXUSCLI_PROVIDER", "deepseek")
    monkeypatch.delenv("NEXUSCLI_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")

    config = load_config(project_root=tmp_path)

    assert config.llm.api_key == "deepseek-key"


def test_feature_config_auto_snapshot_default_off(tmp_path, monkeypatch):
    # auto_snapshot is opt-in: a fresh FeatureConfig and a config load with no
    # features section both default to False, while an explicit section can
    # turn it on.
    assert FeatureConfig().auto_snapshot is False

    home = tmp_path / "home"
    (home / ".nexuscli").mkdir(parents=True)
    project = tmp_path / "project"
    (project / ".nexuscli").mkdir(parents=True)
    # Patch both HOME and USERPROFILE: Path.home() prefers USERPROFILE on
    # Windows, so an unpatched USERPROFILE would leak the developer's real
    # ~/.nexuscli/config.json into this test (win32 gotcha).
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    config = load_config(project_root=project)
    assert config.features.auto_snapshot is False

    (project / ".nexuscli" / "config.json").write_text(
        json.dumps({"features": {"auto_snapshot": True}}),
        encoding="utf-8",
    )
    config = load_config(project_root=project)
    assert config.features.auto_snapshot is True
