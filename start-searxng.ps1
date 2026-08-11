# ローカル LLM 用の SearXNG を起動する。
#
#   powershell -NoProfile -File <searxng-checkout>\start-searxng.ps1
#
# 既定で http://127.0.0.1:8888 に bind する（localhost 専用。外には出さない）。
# 設定は settings-local.yml。JSON 出力はそこで有効化してある。
#
# Windows で動かすために2つ細工がある。詳細は ollama-search の README:
#   1. utils/templates/ を sparse-checkout で除外（ファイル名に ':' があり NTFS で作れない）
#   2. .venv/Lib/site-packages/sitecustomize.py で Unix API（pwd / os.getuid）を補う

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

$existing = Get-NetTCPConnection -LocalPort 8888 -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "SearXNG はすでに 8888 で起動している (PID $($existing.OwningProcess))"
    exit 0
}

$env:SEARXNG_SETTINGS_PATH = Join-Path $root "settings-local.yml"
Write-Host "starting SearXNG -> http://127.0.0.1:8888"
& (Join-Path $root ".venv\Scripts\python.exe") -m searx.webapp
