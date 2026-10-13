# Start a voice session: bring up the voice-chat server and floating ball, then start Claude Code with the voice channel.
# Run it from the folder Claude Code should work in; extra arguments go to claude (e.g. `-c`).
#   powershell -ExecutionPolicy Bypass -File <repo>\scripts\claude-voice.ps1 [-c]

$repo = Split-Path $PSScriptRoot -Parent

function Test-VoicePort {
    try { $c = New-Object System.Net.Sockets.TcpClient; $c.Connect('127.0.0.1', 8766); $c.Close(); return $true } catch { return $false }
}

# Start the server first so it is up while Claude Code is still loading.
if (-not (Test-VoicePort)) {
    $log = "$env:APPDATA\voice-chat"
    New-Item -ItemType Directory -Force $log | Out-Null
    Start-Process uv -ArgumentList 'run', 'voice.py', '--no-ball' -WorkingDirectory $repo `
        -WindowStyle Hidden -RedirectStandardOutput "$log\server.out.log" -RedirectStandardError "$log\server.err.log"
    for ($i = 0; $i -lt 30 -and -not (Test-VoicePort); $i++) { Start-Sleep -Milliseconds 500 }
}
# Show the floating mic ball. Only one ball runs at a time, so this is safe when it is already up.
Start-Process uv -ArgumentList 'run', 'ball.pyw' -WorkingDirectory $repo -WindowStyle Hidden

# The channel server only takes utterances off voice.py's queue in sessions started this way.
$env:VOICE_SERVER_LAUNCHED = '1'
# The local plugin is not on Claude Code's channel allowlist, hence the development flag
# (Claude Code asks for confirmation once at startup).
claude @args --dangerously-load-development-channels plugin:voice@voice-chat-local
