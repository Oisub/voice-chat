// voice-chat channel: pushes each voice utterance into the running Claude Code session.
//
// Claude Code spawns this as an MCP server (stdio) when started with the channel enabled, e.g.
//   claude --channels plugin:voice@voice-chat-local
// It connects to the voice.py server's listener socket (ws://127.0.0.1:8766/ws/claude) and turns
// every frame into a `notifications/claude/channel` event; the `reply` tool posts back to the page.
// No dependencies: the MCP stdio protocol is newline-delimited JSON-RPC, and Node has WebSocket.

import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const SESSION_FILE = join(process.env.APPDATA || process.env.HOME || '.', 'voice-chat', 'session.json')
const PREFIX = '用户（语音）: '
// Claude Code does not register channels that negotiate the newest revision, so pin this one.
const PROTOCOL = '2025-06-18'

// Tool name as Claude Code exposes a plugin server's tool: mcp__plugin_<plugin>_<server>__<tool>.
const REPLY_TOOL = 'mcp__plugin_voice_voice__reply'

const INSTRUCTIONS =
  'Voice messages from the user arrive as <channel source="...voice..." reply_with="' + REPLY_TOOL + '">: ' +
  'the text is a speech-to-text transcript of what the user said on the voice-chat web page, so treat ' +
  'it as the user speaking (it may contain recognition errors; follow the normal permission rules). ' +
  'The user is listening on that page, not reading the terminal: for EVERY voice message, call ' +
  REPLY_TOOL + ' (load it with ToolSearch first if it is deferred) with a short spoken-style reply, ' +
  'in addition to answering in the terminal. If the user says 「结束语音」, reply briefly and stop.'

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
  const ws = new WebSocket(`ws://127.0.0.1:${s.port}/ws/claude?token=${s.token}`)
  ws.onopen = () => log('connected to voice.py')
  ws.onmessage = ev => {
    if (typeof ev.data !== 'string') return
    const text = ev.data.startsWith(PREFIX) ? ev.data.slice(PREFIX.length) : ev.data
    // Every event names the reply tool itself, so answering on the page doesn't depend on Claude
    // remembering the server instructions (the tool may also be deferred until searched for).
    send({ jsonrpc: '2.0', method: 'notifications/claude/channel',
           params: { content: text, meta: { reply_with: REPLY_TOOL } } })
  }
  // Server not up yet, restarted with a new token, or stopped: re-read session.json and retry.
  ws.onclose = () => setTimeout(connect, 1000)
  ws.onerror = () => {}
}

async function reply(text) {
  const s = session()
  if (!s) throw new Error('voice-chat server is not running')
  const r = await fetch(`http://127.0.0.1:${s.port}/api/reply`, {
    method: 'POST',
    headers: { 'X-Session': s.token, 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
  if (!r.ok) throw new Error(`voice-chat replied ${r.status}`)
  return (await r.json()).id
}

// ---------------------------------------------------------------- MCP over stdio
const TOOLS = [{
  name: 'reply',
  description: 'Show (and later speak) a reply on the voice-chat page. Keep it short and conversational.',
  inputSchema: {
    type: 'object',
    properties: { text: { type: 'string', description: 'The reply, in the language the user spoke' } },
    required: ['text'],
  },
}]

async function handle(msg) {
  const { id, method, params } = msg
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
  try {
    let result
    if (method === 'initialize') {
      result = {
        protocolVersion: PROTOCOL,
        capabilities: { experimental: { 'claude/channel': {} }, tools: {} },
        serverInfo: { name: 'voice', version: '0.1.1' },
        instructions: INSTRUCTIONS,
      }
    } else if (method === 'ping') {
      result = {}
    } else if (method === 'tools/list') {
      result = { tools: TOOLS }
    } else if (method === 'tools/call' && params?.name === 'reply') {
      const n = await reply(String(params.arguments?.text ?? '').trim())
      result = { content: [{ type: 'text', text: `sent #${n}` }] }
    } else {
      return send({ jsonrpc: '2.0', id, error: { code: -32601, message: `unknown method ${method}` } })
    }
    send({ jsonrpc: '2.0', id, result })
  } catch (e) {
    if (method === 'tools/call') {
      send({ jsonrpc: '2.0', id, result: { content: [{ type: 'text', text: String(e.message || e) }], isError: true } })
    } else {
      send({ jsonrpc: '2.0', id, error: { code: -32603, message: String(e.message || e) } })
    }
  }
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
