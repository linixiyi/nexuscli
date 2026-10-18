# Round 2 — 站点收尾 lane 证据（site/style.css、site/build.py 头注）

本 lane 改动的文件:`site/style.css`、`site/build.py`(头注 1-10 行)。其余文件零改动。

## 1. .btn-primary 对比度裁决落地

**改动** `site/style.css:250-261`:渐变
`linear-gradient(92deg, var(--accent), #9d7bff 60%, var(--accent-2) 130%)`
→ `linear-gradient(92deg, #7152ff, #7752ff 60%, #0e7490 130%)`,白字、92deg、
3-stop 结构、box-shadow 均不变;只压深 stop,不换设计。起点弃用 `var(--accent)`
(见下:其自身只有 4.35:1),终点弃用 `var(--accent-2)`(1.81:1),改用行内
hex 并加注释说明数值依据(style.css:252-257)。

**计算方法**:WCAG 2.x 相对亮度 + `(L1+0.05)/(L2+0.05)`;渐变按 CSS sRGB
逐通道线性插值,沿渐变线 0%→100% 每 0.1% 采样取最差点。脚本用
`.venv/bin/python` 运行(stdlib,无第三方依赖),关键输出摘录:

```
== OLD style.css:252  linear-gradient(92deg,#7c5cff,#9d7bff 60%,#22d3ee 130%)
OLD: worst=2.411:1 @ 99.4% (#58adf5) -> FAIL
     0%: #7c5cff = 4.346:1     60%: #9d7bff = 3.129:1
    50%: #9876ff = 3.296:1    100%: #57adf5 = 2.413:1
== NEW  linear-gradient(92deg,#7152ff,#7752ff 60%,#0e7490 130%)
NEW: worst=4.735:1 @ 55.1% (#7752ff) -> PASS
     0%: #7152ff = 4.824:1     60%: #7752ff = 4.735:1
    50%: #7652ff = 4.750:1    100%: #3b65c0 = 5.514:1
stop point contrasts: #7152ff 4.824 | #7752ff 4.735 | #0e7490 5.358
```

- ask 给的中段估计 ≈3.13:1 复现为 3.129:1;全路径实际最差在右缘(2.411:1,
  由 130% 处 `--accent-2` 拽低),所以终点 stop 也必须换,仅压中段不够。
- 选色依据:起点 #7152ff = hsl(251,100%,66%),与 incumbent `--accent`
  #7c5cff = hsl(251,100%,68%) 同色相同饱和度、只降 2% 亮度,是同色相能过
  4.5:1 的最亮档(0.66 档=4.824,再亮到 0.68 即 fail);中段 #7752ff 保留
  60% 处微提亮(4.735);终点 #0e7490 与仓库既有 light/print 主题的
  `--accent-2` 值相同(style.css:38、:708),色相仍是 cyan 系,只压深。
- 全路径最差点 4.735:1 ≥ 4.5:1,无采样点低于阈值。

## 2. 删除死代码:[data-theme=dark] .content pre 短类名规则

**改动** `site/style.css:627-629`:原 621-631 的五条规则(`.k/.kn/.kd/.ow/
.nb/.nc`、`.s/.s1/.s2/.sd/.sa/.se`、`.c/.c1/.cm`、`.m/.mi/.mf`、`.nf`,
Pygments 短类名)全部删除,替换为一行注释指明 token 配色来源。

死代码证据链(全部本 session 核实):

1. `site/build.py:103-105`(`codehilite` 配置)`use_pygments: False`,注释
   "emit language-* classes; highlight via highlight.js";`git log -S
   use_pygments -- site/build.py` 只有初始提交 3b44508 —— 站点从未产出过
   Pygments 短类名。
2. 生成产物实测:`grep -o 'class="language-[^"]*"' site/docs/*.html
   site/tutorial.html` 全部为 `language-bash/json/text/...`;全站 HTML 无任何
   `class="k"` 类短名(grep 排除已知类后为零命中)。
3. 运行时配色由 CDN highlight.js 承担:`build.py:196-220` 加载
   highlightjs/cdn-release@11.9.0 + `hljs.highlightAll()`,并按主题切换
   github/github-dark.min.css;下载该 CDN JS 实测含 `classPrefix:"hljs-"`,
   github-dark.min.css 的选择器全为 `.hljs-keyword` 等 —— 与被删的短类名
   选择器永不相交。保留无意义,故删除。

## 3. 构建命令核实与 canonical 裁决

两种命令均在本 session 复现(均先 `git status --porcelain site/` 快照、跑完
再比对,产物**零漂移**,site/*.html 字节不变):

| 命令 | 结果 | 备注 |
|---|---|---|
| (a) `.venv/bin/python site/build.py` | exit=0,13 篇 docs + tutorial 全部构建 | 能跑仅因 `.venv` 里有**计划外手装**的 markdown 3.11.1(见下) |
| (b) `uv run --with markdown,pygments python site/build.py` | exit=0,同样 13+1 全部构建,零漂移 | **canonical**,build.py:5 已注明"唯一支持命令" |

**为什么 canonical 是 (b)**:`markdown` 既不在 `pyproject.toml`
dependencies(pyproject.toml:9-16)也不在 `uv.lock`(`grep 'name =
"markdown"' uv.lock` 零命中);`pygments` 在 lock 里也只是 rich/pytest 的
传递依赖(uv.lock:822、:755)。`--with` 把两个构建依赖显式化、临时化,任何
人重新 clone 后都能跑;(a) 依赖某台机器上恰好有人往 .venv 手装过 markdown,
不可复现。uv.lock 的存在使 (b) 与项目依赖解耦,不会反向污染锁定环境。

**首轮矛盾的解释**:首轮证据 lane-site.md:49 记录当时 `.venv/bin/python -c
"import markdown"` → ModuleNotFoundError(属实);本轮同命令 import 成功,
`.venv/lib/python3.*/site-packages/` 实测有 `markdown-3.11.1.dist-info` 与
`pygments-2.20.0.dist-info` —— 两轮之间有人按首轮建议(lane-site.md:67
「或向 .venv 补装」)向 .venv 补装了 markdown(具体操作者无法回溯)。所以
「exit=0」与「.venv 缺 markdown」各自在各自时点都真,不构成说谎;但正因
(a) 的可用性依赖这种计划外状态,头注已把 (b) 定为唯一 canonical
(build.py:1-10),并明示「do not rely on」(a)。

## 4. PRODUCT.md / README 文档篇数

无需改动,全仓已无「12 篇」:`grep -rn "12 篇\|12篇" --include="*.md"
--include="*.html" --include="*.py"`(排除 .spec/.venv/.git)零命中;
README.md:14 已是「全部 13 篇深度文档」,PRODUCT.md:32「13 篇文档 + index +
tutorial」、PRODUCT.md:52「docs/(13 篇主题文档)」。首轮 lane-docs.md:32
记载 PRODUCT.md 两处已在首轮改为 13,本轮复核确认在盘。

## 契约变更清单

**空**。(1) 渲染字符串:两次构建后 `git status --porcelain site/` 与改前
快照逐行一致,site/*.html 字节不变;(2) 断言: `grep -rln "btn-primary|
9d7bff|569cd6|ce9178|style.css" tests/` 与 `grep -rn "site/" tests/*.py`
均零命中,无任何测试读取本 lane 改动的字符串。全量套件按分工由尾段统一跑,
本 lane 未自行运行。

## 遗留(feedback,不在本 lane 边界内)

- `.brand-logo`(style.css:95-107):白字 `#fff` 压 `linear-gradient(135deg,
  var(--accent), var(--accent-2))`,与 .btn-primary 改前同款问题(最差约
  1.8:1)。属品牌 logotype,WCAG 1.4.3 有 logo 豁免,未动;若日后复用该
  渐变做正文组件,应套用本节的压深 stop。
- markdown/pygments 始终未进 pyproject 依赖,canonical 靠 `--with` 头注
  约定维系;若想更强保证,可加 dev extra(属 pyproject,超出本 lane 边界)。
