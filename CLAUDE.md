# voice-chat

Talk to Claude Code by voice: `voice.html` records the mic, `voice.py` (aiohttp, `uv run`) transcribes
with ElevenLabs Scribe v2 (`ELEVENLABS_API_KEY` user env var) and pushes each utterance to Claude.

Two ways utterances reach Claude:
- **Channel** (sessions started with `scripts/claude-voice.ps1`): the `voice` plugin in `channel/`
  delivers each utterance as a `<channel>` message; reply with its `reply` tool. No Monitor needed.
- **Monitor** (the `/voice` skill in `skills/voice`, for an already-open session): steps below.

## Running a voice session with Monitor (Claude's side)
1. Start the server in the background from this folder: `uv run voice.py`
   (Bash `run_in_background`; opens http://127.0.0.1:8766). Port 8766, token is new on every start.
2. Read the token from `%APPDATA%/voice-chat/session.json` (or the server's first stdout line).
3. Listen with Monitor, ws source `ws://127.0.0.1:8766/ws/claude?token=<token>`, `timeout_ms` 1800000.
   Each event is `用户（语音）: <text>`. Re-arm on expiry — utterances arriving while nobody is
   listening are queued server-side and flushed on reconnect.
4. Reply to the page (as well as in the terminal) with a quoted heredoc so text isn't mangled:
   `uv run voice.py say <<'EOF'` … `EOF`. Keep spoken-conversation replies short.

Every connected listener (channel and Monitor alike) gets every utterance, so don't run both in one session.
The page shows a green dot while a listener is connected, and "Claude 思考中…" until a reply arrives.
Log of all turns: `%APPDATA%/voice-chat/log.jsonl`. Every transcription costs ElevenLabs credits.

## Shutting down
`uv run voice.py stop` stops the server. It also exits by itself when the page has been closed for 60 s,
or when Claude's listener has been gone for 2 min (Monitor re-arms in seconds, so that means Claude exited).
Idle cost is small (~50 MB RAM, ~0 CPU).
