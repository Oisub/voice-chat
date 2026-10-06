# /// script
# requires-python = ">=3.11"
# dependencies = ["aiohttp>=3.9"]
# ///
"""voice-chat: talk to Claude Code by voice through a tiny local web page.

    uv run voice.py                 # serves http://127.0.0.1:8766 and opens it
    uv run voice.py say < reply.txt # Claude posts a reply (text on stdin, or as arguments)
    uv run voice.py stop            # shut the server down

The server also exits on its own once the page has been closed for 60 s, or once Claude Code's
listener has been gone for 2 min (after having connected at least once).

Flow: the page records the mic (click to start, click again to stop) and POSTs the audio here;
it is transcribed with ElevenLabs Scribe v2 (ELEVENLABS_API_KEY from the environment) and shown
on the page. Claude Code listens on ws://127.0.0.1:8766/ws/claude?token=... (Monitor tool):
every utterance is pushed there as one text frame. Utterances that arrive while no listener is
connected are queued and flushed on the next connect, so re-arming the monitor loses nothing.
The channel plugin in channel/ connects to the same socket.

The session token (and port) is written to %APPDATA%/voice-chat/session.json; the page gets it
through a <meta> tag, so other sites cannot drive the API. Conversation log: log.jsonl there.
"""

import asyncio
import json
import os
import secrets
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

import aiohttp
from aiohttp import web

PORT = 8766
PAGE = Path(__file__).with_name("voice.html")
STATE = Path(os.environ.get("APPDATA", Path.home())) / "voice-chat"
SESSION_FILE = STATE / "session.json"
LOG = STATE / "log.jsonl"
STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
MAX_AUDIO = 25 * 1024 * 1024

TOKEN = secrets.token_urlsafe(24)
messages: list[dict] = []        # {id, role: "user"|"claude", text, t}
unread: list[dict] = []          # user messages not yet pushed to Claude
listeners: set[web.WebSocketResponse] = set()
changed = asyncio.Event()

# Auto-shutdown: exit once the page is closed (it long-polls every <=20 s) or Claude Code has gone
# away (its Monitor socket stays closed past the grace period; re-arming takes only seconds).
PAGE_GRACE = 60
CLAUDE_GRACE = 120
last_page = time.monotonic()
claude_gone_since: float | None = None   # None until a listener has connected once


def add(role: str, text: str) -> dict:
    m = {"id": len(messages) + 1, "role": role, "text": text, "t": time.time()}
    messages.append(m)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(m, ensure_ascii=False) + "\n")
    changed.set()
    return m


async def flush_unread() -> None:
    while unread and listeners:
        frame = "用户（语音）: " + unread[0]["text"]
        for ws in list(listeners):
            try:
                await ws.send_str(frame)
            except Exception:
                listeners.discard(ws)
        if listeners:
            unread.pop(0)
    changed.set()


def authed(req: web.Request) -> None:
    if req.headers.get("X-Session") != TOKEN:
        raise web.HTTPForbidden(text=json.dumps({"error": "bad session"}), content_type="application/json")


# ---------------------------------------------------------------- handlers
async def index(_req):
    html = PAGE.read_text(encoding="utf-8").replace("__SESSION__", TOKEN)
    return web.Response(text=html, content_type="text/html")


async def audio(req: web.Request):
    authed(req)
    data = await req.read()
    if not data:
        return web.json_response({"error": "没有录到声音"}, status=400)
    form = aiohttp.FormData()
    form.add_field("model_id", "scribe_v2")
    form.add_field("tag_audio_events", "false")
    ctype = (req.content_type or "audio/webm").split(";")[0]
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
    m = add("user", text)
    unread.append(m)
    await flush_unread()
    return web.json_response(m)


async def reply(req: web.Request):
    authed(req)
    text = ((await req.json()).get("text") or "").strip()
    if not text:
        return web.json_response({"error": "empty"}, status=400)
    return web.json_response(add("claude", text))


async def poll(req: web.Request):
    """Long poll: returns as soon as there is something newer than ?after=, or after ~20 s."""
    global last_page
    authed(req)
    last_page = time.monotonic()
    after = int(req.query.get("after", 0))
    deadline = time.monotonic() + 20
    while len(messages) <= after and time.monotonic() < deadline and not req.query.get("now"):
        changed.clear()
        try:
            await asyncio.wait_for(changed.wait(), deadline - time.monotonic())
        except asyncio.TimeoutError:
            break
    return web.json_response({"messages": messages[after:], "listening": bool(listeners),
                              "queued": len(unread)})


async def claude_ws(req: web.Request):
    if req.query.get("token") != TOKEN:
        raise web.HTTPForbidden()
    global claude_gone_since
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(req)
    listeners.add(ws)
    await flush_unread()
    try:
        async for _ in ws:
            pass
    finally:
        listeners.discard(ws)
        if not listeners:
            claude_gone_since = time.monotonic()
        changed.set()
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
            if now - last_page > PAGE_GRACE:
                shutdown("page closed")
            if claude_gone_since is not None and not listeners and now - claude_gone_since > CLAUDE_GRACE:
                shutdown("Claude Code disconnected")
    task = asyncio.create_task(loop())
    yield
    task.cancel()


# ---------------------------------------------------------------- entry points
def say(text: str) -> None:
    s = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    req = urllib.request.Request(
        f"http://127.0.0.1:{s['port']}/api/reply", method="POST",
        data=json.dumps({"text": text}).encode("utf-8"),
        headers={"X-Session": s["token"], "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        print("sent #" + str(json.loads(r.read())["id"]))


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


def serve() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps({"port": PORT, "token": TOKEN}), encoding="utf-8")
    app = web.Application(client_max_size=MAX_AUDIO)
    app.add_routes([web.get("/", index), web.post("/api/audio", audio), web.post("/api/reply", reply),
                    web.get("/api/poll", poll), web.get("/ws/claude", claude_ws), web.post("/api/stop", stop)])
    app.cleanup_ctx.append(watchdog)
    url = f"http://127.0.0.1:{PORT}"
    print(f"voice-chat on {url}  (listener: ws://127.0.0.1:{PORT}/ws/claude?token={TOKEN})", flush=True)
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)


API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")

if __name__ == "__main__":
    if sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1 and sys.argv[1] == "say":
        say(" ".join(sys.argv[2:]) if len(sys.argv) > 2 else sys.stdin.buffer.read().decode("utf-8"))
    elif len(sys.argv) > 1 and sys.argv[1] == "stop":
        stop_server()
    else:
        if not API_KEY:
            sys.exit("ELEVENLABS_API_KEY is not set")
        serve()
