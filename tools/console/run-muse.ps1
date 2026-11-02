param(
    [Parameter(Mandatory)][string]$Prompt,
    [string]$Effort,
    [int]$MaxSteps,
    [string]$Model
)
# Runs one prompt through `muse exec` inside this repo and records the prompt + settings next to the event log,
# so the Muse console (tools/console/server.py) can show prompt, config, replies, tool steps and usage.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$cfg = Get-Content (Join-Path $PSScriptRoot 'config.json') -Raw | ConvertFrom-Json
if (-not $Effort) { $Effort = $cfg.reasoning_effort }
if (-not $MaxSteps) { $MaxSteps = $cfg.max_model_steps }
if (-not $Model) { $Model = $cfg.model }

$promptPath = (Resolve-Path $Prompt).Path
$stem = [IO.Path]::GetFileNameWithoutExtension($promptPath) + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
$runs = Join-Path $root 'muse-runs'
New-Item -ItemType Directory -Force $runs | Out-Null

$museArgs = @('exec', '--workspace', $root, '--trust-workspace', '--approval-mode', 'never',
    '--reasoning-effort', $Effort, '--max-model-steps', $MaxSteps, '--json', '--prompt-file', $promptPath)
if ($Model) { $museArgs += @('--model', $Model) }
if ($cfg.disable_web_tools) { $museArgs += '--disable-web-tools' }

$meta = [ordered]@{
    run = $stem
    prompt_file = [IO.Path]::GetRelativePath($root, $promptPath)
    model = $Model
    reasoning_effort = $Effort
    max_model_steps = $MaxSteps
    disable_web_tools = [bool]$cfg.disable_web_tools
    command = 'muse ' + ($museArgs -join ' ')
    started = (Get-Date).ToString('o')
    finished = $null
    exit_code = $null
}
$metaPath = Join-Path $runs "$stem.meta.json"
$meta | ConvertTo-Json | Set-Content $metaPath -Encoding utf8

& muse @museArgs > (Join-Path $runs "$stem.jsonl") 2> (Join-Path $runs "$stem.err.txt")
$meta.exit_code = $LASTEXITCODE
$meta.finished = (Get-Date).ToString('o')
$meta | ConvertTo-Json | Set-Content $metaPath -Encoding utf8
"run=$stem exit=$($meta.exit_code)"
