Set-Location -Path $PSScriptRoot
python -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
if (-not (Test-Path -LiteralPath ".\.env")) {
    Copy-Item -LiteralPath ".\.env.example" -Destination ".\.env"
}
Write-Host "Done. Edit .env, set BOT_TOKEN and ADMIN_IDS, then run .\build_index.ps1 and .\run_bot.ps1"
