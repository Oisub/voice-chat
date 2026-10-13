// voice-chat channel: pushes each voice utterance into the running Claude Code session.
//
// Claude Code spawns this as an MCP server (stdio) when started with the channel enabled, e.g.
//   claude --channels plugin:voice@voice-chat-local
// It connects to the voice.py server's listener socket (ws://127.0.0.1:8766/ws/claude) and turns
// every frame into a `notifications/claude/channel` event. Voice input is just another way of typing:
// Claude answers in the terminal as usual, there is no reply path back.
// No dependencies: the MCP stdio protocol is newline-delimited JSON-RPC, and Node has WebSocket.

import { readFileSync } from 'node:fs'
import { basename, join } from 'node:path'

const SESSION_FILE = join(process.env.APPDATA || process.env.HOME || '.', 'voice-chat', 'session.json')
const PREFIX = '用户（语音）: '
// Claude Code does not register channels that negotiate the newest revision, so pin this one.
const PROTOCOL = '2025-06-18'

const INSTRUCTIONS =
  'Messages arriving as <channel source="...voice..."> are the user speaking instead of typing: a ' +
  'speech-to-text transcript of the user\'s own words (it may contain recognition errors). Treat each ' +
  'one exactly like a message the user typed in the terminal and answer in the terminal as usual; ' +
  'follow the normal permission rules. There is no separate reply channel.'

function send(msg) {
  process.stdout.write(JSON.stringify(msg) + '\n')
}

function log(...args) {
  process.stderr.write('[voice-channel] ' + args.join(' ') + '\n')
}

function session() {
  try {
    return JSON.parse(readFileSync(SESSION_FILE, 'utf8'))
  } catch {
    return null
  }
}

// ---------------------------------------------------------------- voice.py listener socket
let ready = false   // Claude Code finished the MCP handshake; only then take utterances off the queue

function connect() {
  const s = session()
  if (!s) return setTimeout(connect, 1000)
  // name: shown in the ball's menu; pid (Claude Code, our parent): lets the ball find the terminal
  // window this session runs in, so an utterance goes only to the session in the focused window.
  const q = `token=${s.token}&name=${encodeURIComponent(basename(process.cwd()))}&pid=${process.ppid}`
  const ws = new WebSocket(`ws://127.0.0.1:${s.port}/ws/claude?${q}`)
  ws.onopen = () => log('connected to voice.py')
  ws.onmessage = ev => {
    if (typeof ev.data !== 'string') return
    const text = ev.data.startsWith(PREFIX) ? ev.data.slice(PREFIX.length) : ev.data
    send({ jsonrpc: '2.0', method: 'notifications/claude/channel', params: { content: text } })
  }
  // Server not up yet, restarted with a new token, or stopped: re-read session.json and retry.
  ws.onclose = () => setTimeout(connect, 1000)
  ws.onerror = () => {}
}

// ---------------------------------------------------------------- MCP over stdio
function handle(msg) {
  const { id, method } = msg
  if (id === undefined) {                       // notification from the client
    // The plugin's server also starts in sessions without the channel enabled, where Claude Code
    // drops channel events silently; only sessions launched for voice (claude-here.ps1 sets
    // VOICE_SERVER_LAUNCHED) may take utterances off voice.py's queue.
    if (method === 'notifications/initialized' && !ready && process.env.VOICE_SERVER_LAUNCHED === '1') {
      ready = true
      connect()
    }
    return
  }
  let result
  if (method === 'initialize') {
    result = {
      protocolVersion: PROTOCOL,
      capabilities: { experimental: { 'claude/channel': {} } },
      serverInfo: { name: 'voice', version: '0.3.0' },
      instructions: INSTRUCTIONS,
    }
  } else if (method === 'ping') {
    result = {}
  } else {
    return send({ jsonrpc: '2.0', id, error: { code: -32601, message: `unknown method ${method}` } })
  }
  send({ jsonrpc: '2.0', id, result })
}

let buf = ''
process.stdin.setEncoding('utf8')
process.stdin.on('data', chunk => {
  buf += chunk
  let i
  while ((i = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, i).trim()
    buf = buf.slice(i + 1)
    if (!line) continue
    try {
      handle(JSON.parse(line))
    } catch (e) {
      log('bad message:', e.message)
    }
  }
})
process.stdin.on('end', () => process.exit(0))
