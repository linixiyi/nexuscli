"""task-0005（spec 2026-11-13_fix-project-trust-boundary）：路径权限规则归一化。

此前 path 规则只对 raw path 字符串做 fnmatch（spec A5）：``./.env``、
``a/../.env``、``.//AGENTS.md``、``src/./x.py`` 这类等价拼写可以绕过针对干净
写法（``.env``、``AGENTS.md``、``src/x.py``）的 deny 规则。修复后 path 匹配
同时考虑 raw 与 POSIX 归一化形态（折叠 ``./``、``a/../``、重复 ``/``）：
任一等价形态命中即视为命中，raw 形态保留、既有规则不回退。

边界（behavior 原文）：

- 绝对路径不得被误当成相对路径：``/etc/.env`` 不命中相对规则 ``.env``；
- 反向同样不放宽：相对 payload 不命中绝对规则；
- symlink 不做文件系统解析（规则匹配保持词法，与 path_guard 的 resolve 分层
  不同），既有行为不回退。

全部离线：直接调用 ``evaluate_permissions``，无网络、无 LLM。
"""

from __future__ import annotations

from nexuscli.config import PermissionsConfig
from nexuscli.policy import evaluate_permissions

# ---------------------------------------------------------------------------
# 等价拼写被与干净写法相同的 deny 规则命中
# ---------------------------------------------------------------------------


def test_dot_prefixed_env_spellings_hit_same_deny_rule() -> None:
    perms = PermissionsConfig(deny=["read_file(.env)"])
    clean = evaluate_permissions(perms, "read_file", {"path": ".env"})
    assert clean is not None and clean.action == "deny"
    assert clean.rule == "read_file(.env)"
    for spelling in ("./.env", "a/../.env"):
        hit = evaluate_permissions(perms, "read_file", {"path": spelling})
        assert hit is not None, spelling
        # 同一条 deny：action 与 rule 文本和干净写法完全一致
        assert (hit.action, hit.rule) == (clean.action, clean.rule), spelling


def test_repeated_slash_agents_md_hits_same_deny_rule() -> None:
    perms = PermissionsConfig(deny=["read_file(AGENTS.md)"])
    clean = evaluate_permissions(perms, "read_file", {"path": "AGENTS.md"})
    assert clean is not None and clean.action == "deny"
    assert clean.rule == "read_file(AGENTS.md)"
    hit = evaluate_permissions(perms, "read_file", {"path": ".//AGENTS.md"})
    assert hit is not None
    assert (hit.action, hit.rule) == (clean.action, clean.rule)


def test_inner_dot_segment_hits_same_deny_rule() -> None:
    perms = PermissionsConfig(deny=["read_file(src/x.py)"])
    clean = evaluate_permissions(perms, "read_file", {"path": "src/x.py"})
    assert clean is not None and clean.action == "deny"
    assert clean.rule == "read_file(src/x.py)"
    hit = evaluate_permissions(perms, "read_file", {"path": "src/./x.py"})
    assert hit is not None
    assert (hit.action, hit.rule) == (clean.action, clean.rule)
    # glob 形式的同一条规则对归一化拼写同样命中（raw 形态本就命中，不回退）
    glob_deny = PermissionsConfig(deny=["read_file(src/**)"])
    hit_glob = evaluate_permissions(glob_deny, "read_file", {"path": "src/./x.py"})
    assert hit_glob is not None and hit_glob.action == "deny"


def test_allow_rule_hits_normalized_spelling_too() -> None:
    # 任一等价形态命中即命中，对 allow 同样成立（deny > ask > allow 优先级不变）
    perms = PermissionsConfig(allow=["read_file(src/x.py)"], deny=["read_file(.env)"])
    hit = evaluate_permissions(perms, "read_file", {"path": "src/./x.py"})
    assert hit is not None and hit.action == "allow" and hit.rule == "read_file(src/x.py)"


# ---------------------------------------------------------------------------
# 不误伤：归一化只折叠等价拼写，不扩大到无关路径
# ---------------------------------------------------------------------------


def test_normalization_does_not_catch_unrelated_legit_paths() -> None:
    perms = PermissionsConfig(deny=["read_file(.env)", "read_file(AGENTS.md)"])
    assert evaluate_permissions(perms, "read_file", {"path": "src/x.py"}) is None
    assert evaluate_permissions(perms, "read_file", {"path": "src/nexuscli/config.py"}) is None
    assert evaluate_permissions(perms, "read_file", {"path": "src/./config.py"}) is None


def test_raw_forms_keep_matching_existing_rules() -> None:
    # raw 形态兼容：既有规则的既有命中一个不少
    perms = PermissionsConfig(ask=["write_file(src/**)"])
    hit = evaluate_permissions(perms, "write_file", {"path": "src/nexuscli/config.py"})
    assert hit is not None and hit.action == "ask"
    windows = evaluate_permissions(perms, "write_file", {"path": "src\\nexuscli\\config.py"})
    assert windows is not None
    deny = PermissionsConfig(deny=["read_file(.env)"])
    assert evaluate_permissions(deny, "read_file", {"path": ".env"}) is not None


# ---------------------------------------------------------------------------
# 绝对路径：不误当成相对路径，也不反向放宽
# ---------------------------------------------------------------------------


def test_absolute_paths_stay_absolute() -> None:
    relative_deny = PermissionsConfig(deny=["read_file(.env)"])
    # 绝对 payload 不命中相对规则
    assert evaluate_permissions(relative_deny, "read_file", {"path": "/etc/.env"}) is None
    assert evaluate_permissions(relative_deny, "read_file", {"path": "/repo/.env"}) is None
    # 反向：相对 payload 不命中绝对规则
    absolute_deny = PermissionsConfig(deny=["read_file(/etc/**)"])
    assert evaluate_permissions(absolute_deny, "read_file", {"path": "etc/passwd"}) is None
    # 绝对规则自身：raw 拼写照常命中，归一化拼写命中同一条规则
    exact = PermissionsConfig(deny=["read_file(/etc/hosts)"])
    assert evaluate_permissions(exact, "read_file", {"path": "/etc/hosts"}) is not None
    for spelling in ("/etc/./hosts", "/etc/../etc/hosts"):
        hit = evaluate_permissions(exact, "read_file", {"path": spelling})
        assert hit is not None, spelling
        assert hit.rule == "read_file(/etc/hosts)", spelling


# ---------------------------------------------------------------------------
# symlink：规则匹配保持词法，不做文件系统解析
# ---------------------------------------------------------------------------


def test_symlink_is_not_resolved_for_rule_matching(tmp_path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    glob_deny = PermissionsConfig(deny=[f"read_file({tmp_path}/real/**)"])
    # 穿 symlink 的拼写不被解析成 real/**：词法上不同就不命中（行为不回退）
    assert evaluate_permissions(glob_deny, "read_file", {"path": f"{link}/x"}) is None
    # 真实拼写照常命中（raw 兼容）
    assert evaluate_permissions(glob_deny, "read_file", {"path": f"{real}/x"}) is not None
    # 含 symlink 段的相对拼写的 ``..`` 仍按词法折叠
    deny = PermissionsConfig(deny=["read_file(.env)"])
    hit = evaluate_permissions(deny, "read_file", {"path": "link/../.env"})
    assert hit is not None and hit.rule == "read_file(.env)"
