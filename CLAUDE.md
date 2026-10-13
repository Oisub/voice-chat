# voice-chat

Talk to Claude Code by voice — speaking is just another way of typing. The floating ball `ball.pyw`
(tkinter + sounddevice; always on top, never takes focus) records the mic, `voice.py` (aiohttp, `uv run`)
transcribes with ElevenLabs Scribe v2 (`ELEVENLABS_API_KEY` user env var) and pushes each utterance to Claude.
Claude answers in the terminal like for typed input; there is no reply path back (the web page and the
`say` / `reply` mechanism were removed on purpose).
Two ways utterances reach Claude:
- **Channel** (sessions started with `scripts/claude-voice.ps1`): the `voice` plugin in `channel/`
  delivers each utterance as a `<channel>` message. No Monitor needed.
- **Monitor** (the `/voice` skill in `skills/voice`, for an already-open session): steps below.

## Running a voice session with Monitor (Claude's side)
1. Start the server in the background from this folder: `uv run voice.py`
   (Bash `run_in_background`; it shows the ball). Port 8766, token is new on every start.
2. Read the token from `%APPDATA%/voice-chat/session.json` (or the server's first stdout line).
3. Listen with Monitor, ws source `ws://127.0.0.1:8766/ws/claude?token=<token>`, `timeout_ms` 1800000.
   Each event is `用户（语音）: <text>`: treat it as if the user had typed it. Re-arm on expiry —
   utterances arriving while nobody is listening are queued server-side and flushed on reconnect.

Each utterance goes to ONE listener: the ball sends the session in the foreground terminal window
(found via AttachConsole on the session's Claude Code pid → console window → its owner, the Windows
Terminal window) or the one pinned in its right-click menu; otherwise voice.py uses the last target,
else the newest listener. Channel listeners send `name` (folder) and `pid` in the ws query; a Monitor
listener can add them too (`&name=<folder>&pid=<claude.exe pid>`), else it shows as "Monitor".
The ball's small dot is green while a listener is connected.
Log of utterances: `%APPDATA%/voice-chat/log.jsonl`; ball errors: `ball.log` there.
Every transcription costs ElevenLabs credits.

## Plugin changes
The installed plugin is a cached copy (`~/.claude/plugins/cache/voice-chat-local/voice/<version>`): after
editing `channel/`, bump the version in `channel/.claude-plugin/plugin.json`, run
`claude plugin marketplace update voice-chat-local` and `claude plugin update voice@voice-chat-local`,
then restart the Claude Code session.

## Shutting down
`uv run voice.py stop` stops the server (the ball's right-click menu can too). It also exits by itself when
the ball has been closed for 60 s, or when Claude's listener has been gone for 2 min (Monitor re-arms in
seconds, so that means Claude exited). The ball exits once the server is gone. Idle cost is small.
