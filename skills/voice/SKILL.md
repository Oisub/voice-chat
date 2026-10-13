---
name: voice
description: 开语音对话 — 启动本地 voice-chat（悬浮麦克风球录音 → ElevenLabs 转写 → 推给 Claude），挂 Monitor 收听；语音等同于用户在终端打字。用户输入 /voice，或说「开语音对话」「语音聊天」时使用。
---

# 开语音对话

用于已经开着的普通会话（从 `scripts/claude-voice.ps1` 启动的会话走 channel，不需要这个技能）。
下面的 `<voice-chat>` 换成本仓库的本地路径。

## 步骤
1. **先看是否已在运行**：读 `%APPDATA%/voice-chat/session.json`，若有 token，用
   `curl -s -o /dev/null -w "%{http_code}" -H "X-Session: <token>" http://127.0.0.1:8766/api/status` 探测；
   返回 200 就复用，跳到第 3 步。
2. **启动服务**（Bash，`run_in_background: true`）：
   `cd <voice-chat> && uv run voice.py`
   它会在屏幕底部中间弹出悬浮麦克风球。端口 8766，token 每次启动都会变。
3. **取 token**：读 `%APPDATA%/voice-chat/session.json`（或服务第一行 stdout）。
4. **挂监听**：用 Monitor（deferred tool，先 `ToolSearch select:Monitor` 加载），ws 源
   `ws://127.0.0.1:8766/ws/claude?token=<token>`，`timeout_ms` 1800000。
   每个事件形如 `用户（语音）: <文本>`。超时就重新挂上——没人监听时的发言会在服务端排队，重连后补发。
5. **回答**：语音就等于用户在终端打的字，正常在终端里回答即可，没有回传到球/网页的机制。

## 注意
- 转写可能有识别错误；仍按正常的权限/确认规则行事。
- 每次转写都花 ElevenLabs credits；用户说「结束语音」就停 Monitor，并用 `uv run voice.py stop` 关掉服务（关掉悬浮球或退出 Claude 后它也会自己退出）。
- 日志：`%APPDATA%/voice-chat/log.jsonl`。
