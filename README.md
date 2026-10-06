# voice-chat

Talk to [Claude Code](https://claude.com/claude-code) by voice. A small local web page records your
microphone, the audio is transcribed with [ElevenLabs Scribe v2](https://elevenlabs.io/speech-to-text),
and each utterance is pushed into your running Claude Code session. Claude's replies show up on the page.

```
browser (voice.html) ──audio──▶ voice.py ──▶ ElevenLabs STT
        ▲                          │
        └──── replies ◀────────────┤ utterances (WebSocket)
                                   ▼
                    Claude Code: channel plugin  or  Monitor (/voice skill)
```

Everything runs on `127.0.0.1:8766`. A random session token (new on every start) protects the API, so
other web pages can't drive it.

## What's in here

| Path | What it is |
| --- | --- |
| `voice.py` | Server (aiohttp, run with `uv`): serves the page, transcribes audio, queues utterances, exits on its own when idle |
| `voice.html` | The page: click the mic (or press Space) to start/stop recording; light/dark theme |
| `channel/` | Claude Code plugin with a **channel** MCP server: utterances arrive in the session as `<channel>` messages, Claude answers with its `reply` tool |
| `.claude-plugin/marketplace.json` | Local marketplace so the plugin can be installed from this folder |
| `skills/voice/` | `/voice` skill: start a voice chat inside an already-open session, listening with the Monitor tool |
| `scripts/claude-voice.ps1` | Launcher: starts the server, opens the page, starts Claude Code with the voice channel |

## Requirements

- Windows (paths use `%APPDATA%`; the rest is portable), [uv](https://docs.astral.sh/uv/), Node 18+, Claude Code
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

The page opens, Claude Code starts with the voice channel, and you just talk. Because the plugin is not
on Claude Code's channel allowlist, it is loaded with `--dangerously-load-development-channels`, and
Claude Code asks you to confirm that once at startup. Handy as an Explorer right-click entry.

**Inside an existing session (Monitor).** Type `/voice` (or say "开语音对话"). Claude starts the
server if needed, listens on the WebSocket with the Monitor tool and answers with `uv run voice.py say`.

**Stopping.** Say 「结束语音」, run `uv run voice.py stop`, or just close the page: the server exits
60 s after the page is closed or 2 min after Claude Code disconnects.

Conversation log: `%APPDATA%/voice-chat/log.jsonl`.
