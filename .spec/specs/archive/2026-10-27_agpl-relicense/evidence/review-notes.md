# 验收补充记录（2026-10-27 主会话）

- 出处注释保真对比：git grep -c 'Ported from kitagent (MIT'——HEAD 42 = 工作树 42
  （换许可不触碰 MIT 上游的法定保留声明）。
- 声明面残留扫描：rg "License-MIT|text = \"MIT\"|^MIT\." README.md pyproject.toml
  docs/ LICENSE → 零命中（exit 1）。src/tests 内其余 "MIT" 字样 = 出处注释 +
  "LIMIT/LIMITED" 字母巧合（rg 无 -w 时的子串命中），非许可声明。
- site/ 重生成：docs/*.md 在 06ae2e3（F10/F11）更新过 /turn-resume 但 site 停在更早的
  e981524——既有失步，本轮重生成顺带追平（site 的既有序言即「与源文件同步」）；
  diff 复核 = 文档同步内容 + 许可相关行，无无关漂移。
- site/build.py 需要 markdown 模块（不在项目依赖），门禁命令用
  `uv run --with markdown` 注入。
- task_0001 verify 第一版两处缺陷（frontend/scripts 路径不存在、awk $2 字段分隔
  错位）在本轮回写修正——verify 命令本身是交付物的一部分。
