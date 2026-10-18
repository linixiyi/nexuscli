# Lane F — 可观测性与媒体 真实运行证据

日期:2026-10-18。全部真实运行均以隔离 HOME(`mktemp` 目录,拷贝真实 `~/.nexuscli/config.json`/`models.json`)驱动真实进程;网关为真实本地网关 `http://localhost:20128/v1`(模型 `cbcn/glm-5.3-flash`,个别表面换用说明见各节)。
机器验证统一命令:`.venv/bin/python -m pytest -q tests/test_lane_f_observability.py tests/test_lane_f_media.py` → **10 passed**(2026-10-18 实跑)。

pty 驱动方式:一次性脚本 `/tmp/lane_f_pty_drive.py`(用户提供的骨架,绝对路径修正后驱动 `.venv/bin/nexuscli` 真实进程,未提交为测试)。单发命令用真实子进程 `HOME=<RUNHOME> .venv/bin/nexuscli -p … --yes`。

---

## 13.1 trace_id 关联 — ✅

**怎么跑的**:RUNHOME-A(`debug_dump=true`),pty 起 REPL,发"用 write_file 在文件 hello.txt 写入内容 trace-lane-f-check",真实网关一轮工具会话(approval 提示按 `y` 批准),随后检查落盘的 audit 与 dump。

**关键输出**(RUNHOME-A 内,同一次会话):
```
audit.jsonl: {"tool_name": "write_file", ..., "approver": "hitl", "trace_id": "7e67abc75b7a4a01"}
dump 请求行×3: x-request-id= 7e67abc75b7a4a01   (20:40:47 / 20:41:32 / 20:41:37)
```
3 次 LLM 请求的 `x-request-id` 与审计记录 `trace_id` 全部相同,且无人手工设置 id(进程内惰性生成)。

**机器验证**:`test_lane_f_observability.py::test_one_turn_all_log_lines_share_one_auto_trace_id` — 一个 executor 回合两条审计记录 + 同会话 chat 请求头共享同一自动生成的 16-hex id。
**结论**:✅ 矩阵可置绿。

## 13.2 OTel span — ✅(含如实边界)

**怎么跑的**:RUNHOME-B(`telemetry.enabled=true`),真实子进程运行注入了真实 OTel SDK TracerProvider + SimpleSpanProcessor→ConsoleSpanExporter(collector 替身,`tracing.py:15-19` 文档化的宿主注入路径)的驱动脚本,CLI 单发 `-p "只回复两个字:好的"`。

**关键输出**(`/tmp/lane_f_out_b.txt`,exit 0,模型回复"好的"):
```
{"name": "llm.chat", "context": {"trace_id": "0x79d9c054…", "span_id": "0xdbde9c49…"}, ...
 "attributes": {"nexuscli.trace_id": "7be9e9d7a5b44d1c",
                "nexuscli.llm.model": "cbcn/glm-5.3-flash",
                "nexuscli.llm.provider": "openai-compatible"}, ...
 "resource": {..., "telemetry.sdk.version": "1.45.0"}}
```
span 生成→导出真实走通,属性含会话 trace_id/模型/provider。**无 collector 时边界**:本环境默认无 exporter,不伪造导出;该边界由机器测试钉死。

**机器验证**:`test_telemetry_enabled_without_collector_turn_completes_and_exports_nothing` — enabled+真实 SDK+无 provider:chat 正常完成,`chat_span` 得到 `NonRecordingSpan`(`is_recording()==False`),`record_outcome` 静默无操作。
**结论**:✅ 矩阵可置绿(边界如实:无 exporter 时不导出、不报错)。

## 13.3 llm.debug_dump — ✅

**怎么跑的**:同 13.1 的 RUNHOME-A(config 里 `llm.debug_dump=true` 走真实 `load_config`→factory 布线)。同轮会话结束检查 `~/.nexuscli/debug/llm/2026-10-18.jsonl`。

**关键输出**(dump 请求行 payload 与 headers):
```
payload keys: ['max_tokens','messages','model','stream','stream_options','temperature','tool_choice','tools']
model: cbcn/glm-5.3-flash | stream: True | tools: 27 | messages roles: ['system','user']
headers: x-request-id= 7e67abc75b7a4a01  authorization= <redacted>
dump 响应行: text= 已写好:`hello.txt` 内容为 `trace-lane-f-check`,读取校验通过。
```
能直接看出请求长什么样(model/messages/tools/stream),凭据已脱敏。

**机器验证**:`test_debug_dump_from_config_wiring_and_dump_shows_request` — 真实 config 文件开启→`create_llm_client` 得到 `debug_dump=True`;dump 请求行含用户消息 marker、`authorization=<redacted>`,响应行含 text/usage。
**结论**:✅ 矩阵可置绿。

## 13.4 /usage stats 落库 — ✅

**怎么跑的**:RUNHOME-A 同一会话,真实一轮工具会话后敲 `/usage stats` → `/exit`;再起**第二个进程**(新 REPL,同 RUNHOME)再次 `/usage stats`。

**关键输出**(两进程数字一致):
```
进程1: turns=1  total tokens=16556  By model: cbcn/glm-5.3-flash ×1  By source: repl ×1
进程2(重启后): turns=1  total tokens=16556  By model: cbcn/glm-5.3-flash ×1  By source: repl ×1
```

**机器验证**:`test_usage_stats_survive_restart_and_render_recorded_numbers` — UsageStore 写入→新实例(模拟重启)读出同一行→`_usage_stats_command` 渲染出模型名与 token 数。
**结论**:✅ 矩阵可置绿。

## 14.1 @image: 本地图片 — ⚠️ blocked(产品代码不可达,非功能缺陷)

**怎么跑的**:
1. RUNHOME-C(config model 换成 `cbai/glm-5v-turbo`,网关 `vision:true` 且名称含 `5v`→`supports_images=True`),pty 发 `@image:local_blue.png 图里是什么颜色和形状?`(本地 512×512 蓝底黄圆 PNG)。两次尝试均被**上游渠道拒绝**:
```
⏺ openai-compatible API returned HTTP 400 … {"code":11128,"msg":"request illegal", …
  "displayTips":{"zh":"这次调用没通过渠道校验,可能不是官方客户端发出的。…"}}
⏺ openai-compatible API returned HTTP 503 … [502]: fetch failed (ECONNRESET …)
```
2. 定位:对该模型发**纯文本** curl 也复现 11128 → 是上游渠道挂了,与图片/nexuscli 无关。CLI 侧表现正确:错误信息可读、不崩、可继续会话。
3. wire 级旁证(绕过 nexuscli 直接 curl 网关):`cbcn/glm-5.2`、`cbcn/kimi-k3-1`、`cbcn/minimax-m3` 带 `image_url` 数据 URL 均成功,回答"黄色"(识别出黄圆)。

**判定**:nexuscli 的图片链路本身(解析→data URL→vision payload)有机器验证覆盖,网关上游也证明能收图;但**今天没有任何一个可用模型能从 nexuscli 端到端发图**:唯一的名称标记命中模型渠道死了,其余 vision 模型全被 `supports_images` 的名称标记门槛拦下(`src/nexuscli/llm/openai_compatible.py:217-222`,该文件只有 io dump 部分在本 lane 边界内,不改)→ 记 blocked + feedback。

**机器验证**:`test_local_image_reaches_vision_request_payload` — 本地 PNG→data URL part(含真实 64×32 metadata),vision 模型 `_build_payload` 携带图片、本地路径不泄漏。
**矩阵建议**:维持 🟡,按 feedback 修复门槛后复跑本节即可置绿。

## 14.2 远程图片 — ⚠️ blocked(同 14.1)

**怎么跑的**:同 RUNHOME-C 会话,`@image:https://raw.githubusercontent.com/python-pillow/Pillow/main/Tests/images/hopper.png 这是一张什么照片?` → 上游 503/502(`getaddrinfo ENOTFOUND www.codebuddy.ai`,上游自身 DNS 故障)。
**wire 级旁证**:curl 网关 `cbcn/glm-5.2` 传同一 https URL → 上游真实抓取并正确描述:"这是一位穿着美国海军军官制服、佩戴军官大檐帽的老人,背景是美国国旗"。URL 直传、无本地下载(机器测试钉死)。
**机器验证**:`test_remote_image_url_passes_through_to_vision_payload` — URL 原样进入 vision payload,无 metadata 块、无本地抓取。
**矩阵建议**:维持 🟡,同 14.1。

## 14.3 非多模态模型降级 — ✅

**怎么跑的**:RUNHOME-E(默认模型 `cbcn/glm-5.3-flash`,名称无 vision 标记 → 非多模态;网关对部分 cbcn 模型标 vision:true 但 nexuscli 按名称判定),`debug_dump=true`,pty 发 `@image:local_blue.png 图里是什么颜色和形状?`;拒绝其 bash 补救调用后模型给出完整回答。

**关键输出**:
```
dump 请求行(真实 wire): [user] '[Image omitted: /private/tmp/lane_f_ws_e/local_blue.png, 512x512]\n 图里是什么颜色和形状?'
模型回答:从图像内容看…纯蓝色的圆形…(可读,无报错,会话不崩)
```
带图不崩、降级提示可读(请求被改写为带尺寸的 `[Image omitted: 路径, 宽x高]` 文本),符合预期。

**机器验证**:`test_non_multimodal_model_degrades_to_readable_text_hint` — 非多模态 `_build_payload` 退化为纯字符串(含路径与 512×512 尺寸),payload 无 base64 泄漏;远程 URL 场景退化为 `[Image omitted: remote image, ?x?]`。
**结论**:✅ 矩阵可置绿。(备注:该模型实际看不到图,回答由文件名/二进制头推断——模型行为,非 CLI 缺陷。)

## 14.4 PDF 读取 — ✅

**怎么跑的**:RUNHOME-F,工作区造两页 PDF(pypdf 合并,页 1 "Ticket No: NX-2026-1018",页 2 "Event: NexusCLI Launch Party"),真实子进程:
`HOME=$RUNHOME .venv/bin/nexuscli -p "读取 ticket.pdf,告诉我票号是什么"`(read_file 只读免批)。

**关键输出**:
```
票号是 **NX-2026-1018**(活动:NexusCLI Launch Party)。
```
两页内容全部答对。

**机器验证**:`test_read_pdf_extracts_both_pages` — 真实 read_file 工具对两页 PDF 输出带行号的两页文本(`1: ticket number A-1024` / `2: approval code ZX-88`)。
**结论**:✅ 矩阵可置绿。

## 14.5 Python 语法诊断 — ✅

**怎么跑的**:RUNHOME-G,真实子进程(`--yes` 批准写工具):
`nexuscli -p "用 write_file 创建文件 typo.py,content 逐字为: def broken(: / pass(有意语法错误,不要修复),写完把 Diagnostics 原样展示给我"`。

**关键输出**:
```
已创建 typo.py,内容未做任何修改。工具返回的 Diagnostics 部分原样如下:
Diagnostics:
  File "/private/tmp/lane_f_ws_g/typo.py", line 1
    def broken(:
               ^
SyntaxError: invalid syntax
```
磁盘文件与指示逐字一致;诊断带行号(line 1)与原因(SyntaxError: invalid syntax)。

**机器验证**:`test_write_file_surfaces_syntax_diagnostic_line_and_reason` + `test_write_file_diagnostics_track_real_file_state` — 真实 write_file 工具输出含 `Diagnostics:`/`line 1`/`SyntaxError`;合法文件无 Diagnostics 块;修复后诊断消失。
**结论**:✅ 矩阵可置绿。

---

## 汇总

| # | 表面 | 机器验证 | 真实运行 | 状态建议 |
|---|------|---------|---------|---------|
| 13.1 | trace_id 关联 | lane_f_observability(自动生成 id 全链路一致) | pty 真实会话 audit==x-request-id | ✅ |
| 13.2 | OTel span | lane_f_observability(无 collector NonRecording 边界) | 真实 SDK provider+exporter 导出成功 | ✅ |
| 13.3 | debug_dump | lane_f_observability(config 布线+请求可见) | 真实 dump 落盘、脱敏、可读 | ✅ |
| 13.4 | usage 落库 | lane_f_observability(重启持久+渲染) | 两进程数字一致 | ✅ |
| 14.1 | 本地图片 | lane_f_media(vision payload) | 上游渠道 11128;wire 级旁证通过 | ⚠️ blocked(feedback) |
| 14.2 | 远程图片 | lane_media(URL 直传) | 上游 502;wire 级旁证通过 | ⚠️ blocked(feedback) |
| 14.3 | 非多模态降级 | lane_f_media(payload 退化) | wire 上 `[Image omitted: …, 512x512]`,不崩可读 | ✅ |
| 14.4 | PDF 读取 | lane_f_media(双页抽取) | 票号/活动名答对 | ✅ |
| 14.5 | 语法诊断 | lane_f_media(行号+原因) | 真实 agent 会话原样展示诊断 | ✅ |
