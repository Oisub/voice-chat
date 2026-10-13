# voice-chat

Talk to [Claude Code](https://claude.com/claude-code) by voice — speaking is just another way of typing.
A small floating mic ball records your microphone, the audio is transcribed with
[ElevenLabs Scribe v2](https://elevenlabs.io/speech-to-text), and each utterance is pushed into your
running Claude Code session as if you had typed it. Claude answers in the terminal as usual.

```
ball.pyw ──audio──▶ voice.py ──▶ ElevenLabs STT
                        │
                        │ utterances (WebSocket)
                        ▼
         Claude Code: channel plugin  or  Monitor (/voice skill)
```

Everything runs on `127.0.0.1:8766`. A random session token (new on every start, stored in
`%APPDATA%\voice-chat\session.json`) protects the API, so web pages can't drive it.

## What's in here

| Path | What it is |
| --- | --- |
| `voice.py` | Server (aiohttp, run with `uv`): transcribes audio, queues utterances, exits on its own when idle |
| `ball.pyw` | Floating mic ball: always on top, never steals focus; click to record / send, drag to move, right-click to stop or close |
| `channel/` | Claude Code plugin with a **channel** MCP server: utterances arrive in the session as `<channel>` messages |
| `.claude-plugin/marketplace.json` | Local marketplace so the plugin can be installed from this folder |
| `skills/voice/` | `/voice` skill: start voice input inside an already-open session, listening with the Monitor tool |
| `scripts/claude-voice.ps1` | Launcher: starts the server and the ball, then Claude Code with the voice channel |

## Requirements

- Windows (the ball uses Win32 calls; paths use `%APPDATA%`), [uv](https://docs.astral.sh/uv/), Node 18+, Claude Code
- An ElevenLabs API key in the `ELEVENLABS_API_KEY` environment variable. Each transcription uses credits.

## Setup

```powershell
git clone https://github.com/Oisub/voice-chat
cd voice-chat

# Install the channel plugin from the bundled local marketplace
claude plugin marketplace add .
claude plugin install voice@voice-chat-local

# Optional: the /voice skill for already-open sessions (edit <voice-chat> in it to this folder)
Copy-Item -Recurse skills\voice $env:USERPROFILE\.claude\skills\
```

## Usage

**Voice session (channel).** From the folder you want Claude Code to work in:

```powershell
powershell -ExecutionPolicy Bypass -File <voice-chat>\scripts\claude-voice.ps1
```

The ball appears at the bottom centre of the screen, Claude Code starts with the voice channel, and you
click the ball, talk, and click again. Because the plugin is not on Claude Code's channel allowlist, it is
loaded with `--dangerously-load-development-channels`, and Claude Code asks you to confirm that at startup
(or add it to `allowedChannelPlugins` in managed settings and use `--channels`).

**Inside an existing session (Monitor).** Type `/voice` (or say "开语音对话"). Claude starts the
server if needed and listens on the WebSocket with the Monitor tool.

**Several sessions.** Each utterance goes to one session only: the one in the terminal window that is in
the foreground when you start recording (the ball never takes focus, so that is the window you were
looking at). Right-click the ball to pin a session instead; a short note next to the ball names the
session each utterance went to. Tabs sharing one Windows Terminal window can't be told apart: the last one used wins.

**The ball.** Dark = ready, grey = Claude is not listening, red = recording (shows seconds), amber =
transcribing. Small dot: green = Claude is listening. Errors show as a short note next to the ball.

**Stopping.** Say 「结束语音」, run `uv run voice.py stop`, or close the ball (right-click): the server exits
60 s after the ball is closed or 2 min after Claude Code disconnects.

Utterance log: `%APPDATA%/voice-chat/log.jsonl`.
