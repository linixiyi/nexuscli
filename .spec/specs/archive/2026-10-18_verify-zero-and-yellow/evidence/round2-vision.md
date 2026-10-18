# 视觉与媒体复验(round 2)真实运行证据

日期:2026-10-18。复验对象:轮间修复「supports_images 改为能力驱动」(src/nexuscli/llm/openai_compatible.py:218-232 三态 `vision_capable`,factory 透传 config.llm.vision,`_parse_models_payload` 解析 capabilities.vision)。
网关:真实本地网关 `http://localhost:20128/v1`(curl `/v1/models` 返回 31 个模型,其中 `cbcn/glm-5.2` `capabilities.vision: true`、`cbai/glm-5v-turbo` `vision: true`)。
隔离:每次真实运行 `RUNHOME=$(mktemp -d)`,拷贝真实 `~/.nexuscli/config.json` + `models.json`,只改 RUNHOME 内配置;pty 驱动一次性脚本 `/tmp/r2vision_pty_drive.py`(按工作规则骨架,绝对路径 `/Users/linyixi/nexuscli/.venv/bin/nexuscli`,child 内 `os.chdir` 到 RUNHOME 工作区),单发命令用真实子进程 `HOME=$RUNHOME .venv/bin/nexuscli -p … --yes`。
测试图:本地 512×512 蓝底黄圆 PNG(`local_blue.png`,PIL 生成,放在 RUNHOME 工作区内满足 workspace 边界校验)。
机器验证:`.venv/bin/python -m pytest -q tests/test_vision_capability.py tests/test_lane_f_media.py` → **12 passed**(2026-10-18 实跑)。

---

## F2@14.1 本地图片端到端 — ✅ pass

**怎么跑的**:RUNHOME-A(`/tmp/r2vision_a.IRkF1X`)config 写入 `"model": "cbcn/glm-5.2"`(网关 vision:true)+ `"vision": true` + `debug_dump: true`;pty 起 REPL(cwd=RUNHOME/ws),发 `@image:local_blue.png 图里是什么颜色和形状?用一句话回答`,观测窗口 180s。

**关键输出**(pty 捕获,单回合直接回答,无任何工具补救):
```
❯ @image:local_blue.png 图里是什么颜色和形状?用一句话回答
⏺ 蓝色背景上有一个黄色的圆形。
```
**wire 证据**(RUNHOME-A `~/.nexuscli/debug/llm/2026-10-18.jsonl` 请求行):
```
model: cbcn/glm-5.2 | user content: list
part: image_url | data:image/png;base64,/9j/4AAQ… | len: 8754 | data-url: True
part: text | '图里是什么颜色和形状?…'
```
数据 URL 真实到达网关,metadata 未泄漏(`_format_content` 剥离),全捕获无 `[Image omitted`、无降级提示;回答内容正确(蓝底黄圆)。
**verdict:pass** — 上轮 blocked(名称门槛拦截 vision 模型)已修复:能力驱动后端到端走通。

## F2@14.2 远程图片端到端 — ❌ fail(环境侧:网关上游拒收一切远程 URL)

**怎么跑的**:同 RUNHOME-A,pty 发 `@image:https://raw.githubusercontent.com/python-pillow/Pillow/main/Tests/images/hopper.png 这是一张什么照片?用一句话回答`,观测窗口 180s。

**关键输出**(模型回答,可读但没拿到图):
```
⏺ 抱歉，我无法直接看到这张图片的内容（未能接收到可读取的图像数据或文件路径），
  请提供图片文件路径或描述一下图片，我再帮您分析。
```
**CLI 侧 wire 证据**(同 dump 文件请求行):URL 原样进入 vision payload、无本地抓取,与修复契约一致:
```
…"image_url":{"url":"https://raw.githubusercontent.com/python-pillow/Pillow/main/Tests/images/hopper.png"}}, {"type": "text", …
```
**绕过 nexuscli 的 wire 对照**(同一网关/模型/payload 形状,curl 直发)——三个不同公网主机全部被上游拒收 HTTP 400 code 11135「图片无法识别,请更换图片重试」:
```
raw.githubusercontent.com/…hopper.png        → 11135 Image not recognized
www.google.com/…googlelogo_color_272x92dp.png → 11135 Image not recognized
upload.wikimedia.org/…PNG_transparency_1.png  → 11135 Image not recognized
```
即:CLI 把 URL 按契约直传,但网关上游当前**抓不了任何远程图片**(数据 URL 正常,见 14.1;round-1 wire 级成功过的同一 hopper URL 也不再复现)。用户可见的端到端(网关答对内容)在本环境无法达成。
**verdict:fail** — 非本轮修复缺陷(CLI 侧直传正确、可读降级),是网关上游远程抓取能力缺失;缺什么见 feedback#2。

## F2@14.3 显式 llm.vision=false 降级复验 — ✅ pass

**怎么跑的**:RUNHOME-B(`/tmp/r2vision_b.85iZaH`)config 写入 `"model": "cbcn/glm-5.2"`(网关 vision:true)+ **`"vision": false`** + `debug_dump: true`;pty 起 REPL 发同一 `@image:local_blue.png` 问题(拒绝其 bash 补救调用);再以真实子进程 `HOME=$RUNHOME_B .venv/bin/nexuscli -p '@image:local_blue.png 图里是什么颜色和形状?用一句话回答' --yes`(cwd=RUNHOME-B/ws)。

**关键输出**(pty:模型思考原文引用降级提示,会话不崩;审批拒绝循环正常):
```
  … the image content is omitted from my view. I cannot see the actual image content.
  [Image omitted: /private/tmp/r2vision_b.85iZaH/ws/local_blue.png, 512x512]
⏺ bash(command=ls -la …/local_blue.png && file …)   →  Approve? …: n   (正常拒绝,无崩溃)
```
**单发子进程**(exit 0,回答可读):
```
图片背景为蓝色，中心是一个居中的黄色圆形。
EXIT=0
```
(模型如实说看不到图、经工具自检文件后作答——用户可见行为可读、不崩。)
**wire 证据**(RUNHOME-B dump 请求行):显式开关驱动降级,payload 退化为纯字符串、无 base64 泄漏:
```
user content type: str
user content: [Image omitted: /private/tmp/r2vision_b.85iZaH/ws/local_blue.png, 512x512]
```
名称启发式覆盖(显式 false 压过带标记模型名)由单测钉死:`tests/test_vision_capability.py::test_explicit_vision_false_beats_name_heuristic`(实跑通过)。
**verdict:pass** — 显式能力开关路径端到端生效:不崩、提示可读、行为与 round-1 已验证的降级面一致。

## V@/model 能力徽标 — ❌ fail(fetch 列表徽标不含视觉项;添加后才可见)

**怎么跑的**:RUNHOME-A,pty 驱动 `/model` → Tab×2 到「API 拉取」→ `r` → 回车接受默认地址(真实拉取 31 个模型)→ 查看 fetch 列表行;再在 `cbcn/glm-5.2` 行回车添加,重开 `/model` 看 Custom 列表。

**关键输出**(fetch 列表,vision:true 的行**无任何视觉标识**,全场只有 思考可关/强度✓):
```
> [ ] cbcn/glm-5.2 · ctx 1,000,000 · 思考可关          ← 网关 vision:true
  [ ] cbai/glm-5v-turbo · ctx 200,000 · 思考可关       ← 名称+能力双 vision:true
  [ ] cbai/deepseek-v4-pro · ctx 1,000,000 · 强度✓ · 思考可关
```
**添加后路径正常**(能力已解析并持久化,仅徽标缺失):
```
已添加 1 个模型到 Custom 列表 (/tmp/r2vision_a.IRkF1X/.nexuscli/models.json)
已切换到：cbcn/glm-5.2
> API · cbcn/glm-5.2 ✓
    思考可关闭；支持图片输入 (@image:)
```
落盘确认:`models.json` 中该模型 `"vision": true, "description": "思考可关闭；支持图片输入 (@image:)"`。
**verdict:fail** — 按本项验收「若选择器本就有能力徽标,验证徽标含视觉项」:徽标存在(`src/nexuscli/entrypoints/model_selector.py:234-245` `_capability_badges`)但只渲染 强度✓/思考可关,不含视觉项;用户要在拉取列表里直接看视觉能力则看不到,添加后才在描述里出现(`model_cmds.py:211-212`)。该文件在 entrypoints/,不在本 lane 可改边界(image/、llm/)内 → 记 feedback#1,不改。

---

## 汇总

| 项 | 真实运行 | 机器验证 | verdict |
|----|---------|---------|---------|
| F2@14.1 本地图片 | pty 真实网关答「蓝色背景上有一个黄色的圆形」;wire data URL 8754B | test_vision_capability + test_lane_f_media(12 passed) | ✅ pass |
| F2@14.2 远程图片 | CLI 直传正确(wire);网关上游 11135 拒收 3 个公网主机 | 同上(URL 直传单测通过) | ❌ fail(环境侧) |
| F2@14.3 显式 vision=false | pty + 单发子进程均不崩可读;wire 退化为 `[Image omitted: …, 512x512]` | test_explicit_vision_false_beats_name_heuristic | ✅ pass |
| V@/model 能力徽标 | pty 真拉取 31 模型:fetch 行无视觉徽标;添加后描述含「支持图片输入」 | — | ❌ fail(徽标缺失) |

## Feedback

1. **V@/model**:`src/nexuscli/entrypoints/model_selector.py:234-245` `_capability_badges` 无视觉徽标 —— 网关 `capabilities.vision: true` 的模型在「API 拉取」列表行上没有任何视觉标识(能力数据已被解析并随 `ModelListingProfile.vision` 传递,添加后描述才会显示「支持图片输入 (@image:)」,`model_cmds.py:211-212`)。建议补一行 tri-state 徽标(如 `if profile.vision: badges += " · 视觉✓"`)。entrypoints/ 不在本 lane 文件边界内,未改。
2. **F2@14.2**:网关上游当前拒收一切远程图片 URL(code 11135,含 round-1 成功过的 hopper.png;数据 URL 正常)。nexuscli 侧契约行为(URL 直传、不本地抓取,`image/processor.py:36-37`)已由 wire dump 证实正确;待上游恢复远程抓取后复跑本节即可置绿。若产品上希望摆脱对上游抓取的依赖,可考虑远程 URL 本地下载回退——属设计变更,超出本轮修复范围,仅记录。
3. (观察,非缺陷)glm-5.2 thinking 轮内单回合常超 2 分钟,交互式 pty 下「拒绝工具补救后的最终回答」在 150s 窗口内未出现;单发 `-p --yes` 模式可稳定取到最终回答,复验建议沿用。
