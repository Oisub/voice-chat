# /// script
# requires-python = ">=3.11"
# dependencies = ["aiohttp>=3.9"]
# ///
"""voice-chat: talk to Claude Code by voice. Speaking is just another way of typing.

    uv run voice.py                 # serves 127.0.0.1:8766 and shows the floating mic ball (ball.pyw)
    uv run voice.py --no-ball       # server only (the launcher scripts start the ball themselves)
    uv run voice.py stop            # shut the server down

Flow: the ball records the mic (click to start, click again to stop) and POSTs the audio here;
it is transcribed with ElevenLabs Scribe v2 (ELEVENLABS_API_KEY from the environment) and pushed
to ONE Claude Code session. Sessions listen on ws://127.0.0.1:8766/ws/claude?token=...&name=...&pid=...
(channel plugin in channel/, or the Monitor tool); each utterance is one text frame to one of them.
The ball picks the target (the session in the terminal window that is in the foreground, or the one
pinned in its menu) and sends it as X-Target; without one the last target, else the newest session
gets it. Utterances that arrive while no session is connected are queued and flushed to the next one
that connects, so re-arming a Monitor loses nothing. Claude answers in the terminal; nothing goes back.

The server exits on its own once the ball has been closed for 60 s, or once Claude Code's listener
has been gone for 2 min (after having connected at least once).

The session token (and port) is written to %APPDATA%/voice-chat/session.json; the ball reads it from
there, so other programs (web pages) cannot drive the API. Log of utterances: log.jsonl there.
"""

import asyncio
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import aiohttp
from aiohttp import web

PORT = 8766
BALL = Path(__file__).with_name("ball.pyw")
STATE = Path(os.environ.get("APPDATA", Path.home())) / "voice-chat"
SESSION_FILE = STATE / "session.json"
LOG = STATE / "log.jsonl"
STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
MAX_AUDIO = 25 * 1024 * 1024

TOKEN = secrets.token_urlsafe(24)
unread: list[str] = []           # utterances not yet pushed to Claude
listeners: dict[int, dict] = {}  # id -> {ws, id, name, pid}
next_id = 1
last_target: int | None = None   # session that got the previous utterance

# Auto-shutdown: exit once the ball is closed (it polls /api/status every 2 s) or Claude Code has gone
# away (its listener socket stays closed past the grace period; re-arming takes only seconds).
UI_GRACE = 60
CLAUDE_GRACE = 120
last_ui = time.monotonic()
claude_gone_since: float | None = None   # None until a listener has connected once


def log_utterance(text: str) -> None:
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"role": "user", "text": text, "t": time.time()}, ensure_ascii=False) + "\n")


def pick(requested: int | None) -> dict | None:
    for i in (requested, last_target):
        if i in listeners:
            return listeners[i]
    return listeners[max(listeners)] if listeners else None


async def flush_unread(requested: int | None = None) -> dict | None:
    """Push queued utterances to one session; returns that session (None if nobody is listening)."""
    global last_target
    while unread:
        target = pick(requested)
        if not target:
            return None
        try:
            await target["ws"].send_str("用户（语音）: " + unread[0])
        except Exception:
            listeners.pop(target["id"], None)
            continue
        unread.pop(0)
        last_target = target["id"]
    return listeners.get(last_target)


def authed(req: web.Request) -> None:
    if req.headers.get("X-Session") != TOKEN:
        raise web.HTTPForbidden(text=json.dumps({"error": "bad session"}), content_type="application/json")


# ---------------------------------------------------------------- handlers
async def audio(req: web.Request):
    authed(req)
    data = await req.read()
    if not data:
        return web.json_response({"error": "没有录到声音"}, status=400)
    form = aiohttp.FormData()
    form.add_field("model_id", "scribe_v2")
    form.add_field("tag_audio_events", "false")
    ctype = (req.content_type or "audio/wav").split(";")[0]
    form.add_field("file", data, filename="speech." + ctype.split("/")[-1], content_type=ctype)
    async with aiohttp.ClientSession() as s:
        async with s.post(STT_URL, data=form, headers={"xi-api-key": API_KEY}) as r:
            body = await r.json(content_type=None)
            if r.status != 200:
                detail = body.get("detail", body) if isinstance(body, dict) else body
                msg = detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail)
                return web.json_response({"error": f"转写失败（{r.status}）：{msg}"}, status=502)
    text = (body.get("text") or "").strip()
    if not text:
        return web.json_response({"error": "没听清，再说一次？"}, status=422)
    log_utterance(text)
    unread.append(text)
    try:
        requested = int(req.headers.get("X-Target", ""))
    except ValueError:
        requested = None
    target = await flush_unread(requested)
    return web.json_response({"text": text, "target": target and target["name"]})


async def status(req: web.Request):
    global last_ui
    authed(req)
    last_ui = time.monotonic()
    sessions = [{k: v for k, v in l.items() if k != "ws"} for l in listeners.values()]
    return web.json_response({"listening": bool(listeners), "sessions": sessions,
                              "last": last_target, "queued": len(unread)})


async def claude_ws(req: web.Request):
    if req.query.get("token") != TOKEN:
        raise web.HTTPForbidden()
    global claude_gone_since, next_id
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(req)
    try:
        pid = int(req.query.get("pid", ""))
    except ValueError:
        pid = None   # e.g. a Monitor listener: it can only be the fallback or a pinned target
    me = {"ws": ws, "id": next_id, "name": req.query.get("name") or "Monitor", "pid": pid}
    next_id += 1
    listeners[me["id"]] = me
    await flush_unread(me["id"])
    try:
        async for _ in ws:
            pass
    finally:
        listeners.pop(me["id"], None)
        if not listeners:
            claude_gone_since = time.monotonic()
    return ws


async def stop(req: web.Request):
    authed(req)
    shutdown("stop requested")


def shutdown(reason: str) -> None:
    print(f"voice-chat exiting: {reason}", flush=True)
    SESSION_FILE.unlink(missing_ok=True)
    os._exit(0)


async def watchdog(_app):
    async def loop():
        while True:
            await asyncio.sleep(5)
            now = time.monotonic()
            if now - last_ui > UI_GRACE:
                shutdown("ball closed")
            if claude_gone_since is not None and not listeners and now - claude_gone_since > CLAUDE_GRACE:
                shutdown("Claude Code disconnected")
    task = asyncio.create_task(loop())
    yield
    task.cancel()


# ---------------------------------------------------------------- entry points
def stop_server() -> None:
    if not SESSION_FILE.exists():
        print("not running")
        return
    s = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    req = urllib.request.Request(f"http://127.0.0.1:{s['port']}/api/stop", method="POST",
                                 headers={"X-Session": s["token"]})
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass   # the server exits mid-request
    SESSION_FILE.unlink(missing_ok=True)
    print("stopped")


def start_ball() -> None:
    """Show the floating mic ball (it waits for session.json and exits when this server is gone)."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(["uv", "run", str(BALL)], cwd=BALL.parent, creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def serve() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps({"port": PORT, "token": TOKEN}), encoding="utf-8")
    app = web.Application(client_max_size=MAX_AUDIO)
    app.add_routes([web.post("/api/audio", audio), web.get("/api/status", status),
                    web.get("/ws/claude", claude_ws), web.post("/api/stop", stop)])
    app.cleanup_ctx.append(watchdog)
    print(f"voice-chat on 127.0.0.1:{PORT}  (listener: ws://127.0.0.1:{PORT}/ws/claude?token={TOKEN})", flush=True)
    if "--no-ball" not in sys.argv and "--no-browser" not in sys.argv:
        start_ball()
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)


API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")

if __name__ == "__main__":
    if sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1 and sys.argv[1] == "stop":
        stop_server()
    else:
        if not API_KEY:
            sys.exit("ELEVENLABS_API_KEY is not set")
        serve()
