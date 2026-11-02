param([string]$Log)
# Live, readable view of a `muse exec --json` event log (defaults to the newest log in muse-runs/).
if (-not $Log) {
    $Log = (Get-ChildItem (Join-Path $PSScriptRoot '..\muse-runs\*.jsonl') | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
}
Write-Host "Watching $Log  (Ctrl+C to stop)" -ForegroundColor Cyan
Get-Content $Log -Wait -Tail 400 | ForEach-Object {
    try { $e = $_ | ConvertFrom-Json -ErrorAction Stop } catch { return }
    $p = $e.payload
    switch ($e.payload_type) {
        'run.output.delta' { Write-Host -NoNewline $p.text -ForegroundColor White }
        'tool.result' {
            $first = ($p.text -split "`n")[0]
            if ($first.Length -gt 160) { $first = $first.Substring(0, 160) + '...' }
            Write-Host "`n  [tool] $first" -ForegroundColor DarkYellow
        }
        'task.lifecycle.side_effect_intent' {
            if ($p.event.operation -eq 'model.meta.response') { Write-Host "`n  [model call]" -ForegroundColor DarkCyan }
            else { Write-Host "`n  [action] $($p.event.operation)" -ForegroundColor Yellow }
        }
        'task.lifecycle.failed' { Write-Host "`n  [failed] $($p.event.reason)" -ForegroundColor Red }
        'run.usage' {
            foreach ($m in $p.models) {
                Write-Host "`n  [usage] $($m.model): in=$($m.input_tokens) cached=$($m.cached_tokens) out=$($m.output_tokens) calls=$($m.calls)" -ForegroundColor Green
            }
        }
        'run.terminal.completed' { Write-Host "`n=== RUN COMPLETED ===" -ForegroundColor Green }
    }
}
