# Lane E1:site/style.css 双主题设计审计与 polish

日期:2026-10-19 · 分支:spec/2026-10-19_release-020-design-audit
改动源文件:**仅 site/style.css**(site/docs/testing-matrix.html 为重建产物对齐既有漂移,见 §3.5;其他所有文件未动)。

## 1. 审计方法(实际执行)

- **对比度**:从 site/style.css 的 `:root`(dark)与 `[data-theme="light"]` 块提取全部 6 位 hex 变量,按 WCAG 相对亮度公式计算前景/背景对比度(内联 python 脚本,改动前、改动后各跑一遍)。
- **heading 层级**:`grep -o '<h[1-6]'` 逐一检查 index.html、tutorial.html、全部 docs/*.html。
- **语法高亮实际路径**:build.py:99-102 配置 `use_pygments: False` + 浏览器端 highlight.js CDN 11.9.0(github / github-dark 双主题,shell() 内按 data-theme 切换)。`curl` 下载两份 CDN css,逐条文本规则按其实际 background 计算。
- 死代码核查:`grep 'class="k"\|class="s"\|class="nf"' site/docs/*.html site/tutorial.html` → 0 命中。

### 改动前的发现(计算输出摘录)

| token | DARK 最差值 | LIGHT 最差值 | 判定 |
|---|---|---|---|
| --text(正文/代码继承) | 13.04(surface_2) | 13.69(code_bg) | 两主题全部背景 ✅ |
| --muted | 5.64 | 5.03 | ✅ |
| --faint | 3.60(surface) | **2.74(code_bg)** | **两主题均 <4.5 ❌**(doc-meta、toc-title、footer、pager .dir、edit-link、term-bar em 等小字) |
| --link | 8.34 | 5.53 | ✅ |
| --accent-2 | 9.32 | **1.61**(light 未覆盖,继承 #22d3ee;kicker、toc 激活项、stat 数值、term t-a/t-code) | **light ❌** |
| --green(term t-p/t-ok) | 9.89 | **1.72**(light 继承 #34d399) | **light ❌** |
| .t-w/.t-tool 硬编码 #f5c26b | 11.60 | **1.46** | **light ❌** |

其余审计项:

- **heading**:每页恰好 1 个 h1,序列 h1→h2→h3,无跳级(index.html 44/83/89…、tutorial.html、docs/*.html 全部合规)。
- **focus**:改动前无任何 `:focus-visible` 规则,键盘焦点仅剩浏览器默认描边。
- **链接可辨**:正文链接仅靠颜色区分(WCAG 1.4.1),无下划线。
- **移动端**:980px/640px 两档断点存在;640px 下 `.pager` 仍两列(375px 屏过窄),`.nav-inner` gap 22px 在 320px 屏溢出(113+22+86+22+34=277 > 272 可用)。
- **打印**:改动前无 `@media print`;暗色变量会打出近黑背景,nav/toc/pager 会打印,且 `.js-reveal .rv` 未触发时整节打印为空白(style.css:660 隐藏态)。

## 2. 问题 → 改动(全部在 site/style.css)

| # | 问题 | 改动(现行号) |
|---|---|---|
| 1 | light `--faint` #8894a6 对 bg/code-bg 仅 2.85/2.74 | → `#5f6b7d`(4.83–5.40),style.css:36 |
| 2 | dark `--faint` #647089 对 surface 仅 3.60 | → `#7c88a0`(4.72–5.45),仍暗于 `--muted`,style.css:13 |
| 3 | light 继承 `--accent-2` #22d3ee,文字对比 1.61–1.81 | light 新增 `--accent-2: #0e7490`(4.78–5.36),style.css:38 |
| 4 | light 继承 `--green` #34d399,term 提示色 1.72 | light 新增 `--green: #047857`(4.90–5.48),style.css:39 |
| 5 | `.t-w`/`.t-tool` 硬编码 #f5c26b,light 下 1.46 | 新增 `--warn`(dark #f5c26b / light #92600a),两规则改 `var(--warn)`,style.css:17,40,298,303 |
| 6 | 无键盘焦点可见态 | 新增 `:focus-visible { outline: 2px solid var(--accent-2); outline-offset: 2px }`,style.css:65 |
| 7 | 正文链接仅靠颜色区分 | 新增 `.content :is(p, li, td, th, blockquote) a` 下划线,style.css:563 |
| 8 | 无打印样式 | 新增 `@media print` 块(style.css:698-727):强制浅色变量、隐藏 .nav/.toc/.pager/.edit-link/.cta-band/.hero::before/.term-cursor、强制 `.rv` 可见、pre 改 pre-wrap 防截断、pre/blockquote/tr/卡片 break-inside:avoid、`.grad` 渐变标题退化实色 |
| 9 | 640px 下 pager 两列过窄、nav 320px 溢出 | 640px 断点新增 `.pager` 单列、`.nav-inner { gap: 12px }`,style.css:685-696 |

## 3. 验证(实际跑过的命令与输出摘录)

1. **构建**:ask 字面命令 `.venv/bin/python site/build.py` 在当前环境不可跑 —— `.venv/bin/python -c "import markdown"` → `ModuleNotFoundError: No module named 'markdown'`(预先存在的环境事实,与本 lane 改动无关;且 `uv run --with markdown,pygments .venv/bin/python …` 同样失败,uv 的 `--with` 不注入显式指定的 venv 解释器)。改用 build.py:3 头注的文档命令:
   `uv run --with markdown,pygments python site/build.py` → 打印 13 个 `docs/*.html  <-  docs/*.md` + `tutorial.html  <-  TUTORIAL.md`,结尾 `done.`,退出码 0。
   注:DOC_META(build.py:19-33)、PAGER_ORDER、docs/*.md 均为 **13** 篇;ask 写"12 篇",实际 13,已全量产出。
2. **产物核对**:`ls site/docs/*.html | wc -l` → 13;site/index.html、site/tutorial.html 存在;两页 + testing-matrix.html 各含 1 条 style.css 链接,且均含 `data-theme="dark"` 初始化与 `nexuscli-theme`/`prefers-color-scheme` 脚本(双主题可达)。testing-matrix.html 含 `<table`/`<pre><code` 结构,站点表格与代码样式对其生效。
3. **复检对比度**(从改动后 style.css 重新提取变量计算):两主题全部文本 token 组合(text/muted/faint/link/accent-2/green/warn × bg/bg_soft/surface/surface_2/code_bg)≥ 4.5,**最差 4.70**;`ALL >= 4.5: True`;层级保持(dark faint 仍暗于 muted、light faint 仍亮于 muted,均 True);花括号 178/178 平衡。
4. **代码块实际着色**:github-dark base `#c9d1d9 on #0d1117 = 12.26`,github-light base `#24292e on #ffffff = 14.67`;两文件逐条文本规则(含 addition/deletion 自带背景配对)无 <4.5,输出 `text rules below 4.5 (vs own bg): none`。
5. **渲染契约**:build.py 未改动一行;`grep -rn "NexusCLI 文档\|generated-banner\|themeToggle\|doc-card\|tutorial.html" tests/ src/` → 0 命中(无测试断言站点渲染字符串);git status 中 site/ 下仅 style.css(本 lane 改动)与 docs/testing-matrix.html。**契约变更清单:空。**
6. **testing-matrix.html 的 diff 属既有漂移**:已提交的 HTML 落后于已提交的 docs/testing-matrix.md(commit 5d0c261 加入收口轮内容但站点未重建);本次重建把产物对齐到当前 md,md 本身零改动(git status 无 docs/*.md)。

## 4. 结论

双主题正文与代码块对比度全部 ≥4.5:1(CSS 变量推算 + CDN 主题逐 token 复算);focus 可见态、正文链接可辨性、打印可用性、640px 断点经审计后以最小 CSS refinement 落地;heading 层级本就合规,未动;构建成功,产物 13 docs + tutorial + index 齐全且与 docs/ 源一致。

## 5. 遗留(feedback,未改)

1. `.btn-primary` 白字压在渐变中段 `#9d7bff` 上 ≈ 3.13(style.css:248 附近,index 英雄区 CTA,15px/600)。属品牌视觉改动,超出本次枚举审计范围。
2. style.css:621-631 的 `[data-theme="dark"] .content pre .k/.s/…` 为死代码(渲染管线实际走 highlight.js CDN 长类名),建议后续删除或注明保留原因。
3. CDN 主题自带 `.hljs` 背景(#0d1117/#fff)与站点 `--code-bg`(#0c101a/#f0f2f7)有轻微双色调差,纯外观,对比度无问题。
4. `.venv` 缺 markdown 模块,ask 字面命令不可用;建议主线程统一用 `uv run --with markdown,pygments python site/build.py`(build.py 头注命令)或向 .venv 补装。
