# token-lab install (Windows). One command (vX.Y.Z = the latest release tag, see README):
#   irm https://raw.githubusercontent.com/merttrkr/claude-token-kit/vX.Y.Z/install.ps1 | iex
# What it does: checks for claude, installs uv with winget if missing, adds and installs the plugin,
# turns on auto-update for token-kit (the field the /plugin > Marketplaces toggle writes).
# Updates what is already installed; touches no other setting.
# Runs in the caller's PowerShell (irm | iex), so everything below is in a child
# scope with its own error preference: native stderr never stops it, and the
# caller's session is left as it was. Each command's exit code is checked.

& {
$ErrorActionPreference = 'Continue'

$Repo = 'https://github.com/merttrkr/claude-token-kit'

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
        [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Invoke-Claude {
    # Runs `claude <args>`; on failure prints what claude said, so the user sees why.
    $said = (& claude @args 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        if ($said) { Write-Host $said }
        return $false
    }
    return $true
}

if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Host 'Claude Code is not installed (or this window cannot find it).' -ForegroundColor Red
    Write-Host 'Install it from https://claude.com/claude-code, open a new PowerShell window, and paste the install command again.'
    return
}

if (-not (Get-Command uvx -ErrorAction SilentlyContinue)) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host 'token-lab needs a small tool called uv, and this PC has no winget to install it.' -ForegroundColor Red
        Write-Host 'Install uv from https://docs.astral.sh/uv/getting-started/installation/ , open a new PowerShell window, and paste the install command again.'
        return
    }
    Write-Host 'Installing uv, a small tool token-lab runs on (with winget; 1-2 minutes)...'
    $said = (winget install --id astral-sh.uv -e --accept-package-agreements --accept-source-agreements --silent 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        if ($said) { Write-Host $said }
        Write-Host 'Could not install uv with winget (message above).' -ForegroundColor Red
        Write-Host 'Install uv from https://docs.astral.sh/uv/getting-started/installation/ , open a new PowerShell window, and paste the install command again.'
        return
    }
    Refresh-Path
    if (-not (Get-Command uvx -ErrorAction SilentlyContinue)) {
        Write-Host 'uv is installed, but this window cannot see it yet.' -ForegroundColor Yellow
        Write-Host 'Close this PowerShell window, open a new one, and paste the install command again.'
        return
    }
}

Write-Host 'Adding token-lab to Claude Code...'
$markets = (& claude plugin marketplace list 2>&1 | Out-String)
if ($markets -match 'token-kit') {
    $ok = Invoke-Claude plugin marketplace update token-kit
} else {
    $ok = Invoke-Claude plugin marketplace add $Repo
}
if (-not $ok) {
    Write-Host 'Could not reach the token-lab catalog on GitHub (message above). Check the internet connection and paste the install command again.' -ForegroundColor Red
    return
}

$plugins = (& claude plugin list 2>&1 | Out-String)
if ($plugins -match 'token-lab@token-kit') {
    $ok = Invoke-Claude plugin update token-lab@token-kit
} else {
    $ok = Invoke-Claude plugin install token-lab@token-kit
}
if (-not $ok) {
    Write-Host 'Could not install token-lab (message above). Paste the install command again; if it fails twice, open an issue on GitHub.' -ForegroundColor Red
    return
}

if ((& claude plugin list 2>&1 | Out-String) -notmatch 'token-lab@token-kit') {
    Write-Host 'Install could not be confirmed: Claude Code does not list token-lab. Paste the install command again.' -ForegroundColor Red
    return
}

# An update that failed leaves the old version listed: compare with the marketplace.
$ConfigDir = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $HOME '.claude' }
$latest = $null; $installed = $null
try {
    $manifest = Join-Path $ConfigDir 'plugins/marketplaces/token-kit/.claude-plugin/marketplace.json'
    $latest = ((Get-Content $manifest -Raw | ConvertFrom-Json).plugins | Where-Object { $_.name -eq 'token-lab' }).version
    $installed = ((& claude plugin list --json 2>$null | Out-String | ConvertFrom-Json) | Where-Object { $_.id -eq 'token-lab@token-kit' }).version
} catch {}
if ($latest -and $installed -and $latest -ne $installed) {
    Write-Host "The update did not finish: version $installed is installed, $latest is the latest." -ForegroundColor Red
    Write-Host 'Paste the install command again in this window.'
    return
}

# Auto-update: the same field the /plugin toggle writes, on the token-kit entry only.
$AutoUpdate = @'
import json, os, sys, time
p = sys.argv[1]
lock = p + ".lock"  # Claude Code's own lock: a folder next to the file
try:
    for _ in range(50):
        try:
            os.mkdir(lock)
            break
        except FileExistsError:
            time.sleep(0.1)
    else:
        raise TimeoutError(lock)
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        e = d["token-kit"]
        if "autoUpdate" not in e:  # never undo a user's own "off"
            e["autoUpdate"] = True
            with open(p + ".tmp", "w", encoding="utf-8") as f:
                json.dump(d, f, indent=2, ensure_ascii=False)
            os.replace(p + ".tmp", p)
        result = "on" if e["autoUpdate"] else "user-off"
    finally:
        os.rmdir(lock)
    print(result)
except Exception:
    print("off")
'@
$Known = Join-Path $ConfigDir 'plugins\known_marketplaces.json'
Write-Host 'Turning on automatic updates (the first time this may download Python; up to a minute)...'
$auto = ($AutoUpdate | uv run --no-project --quiet python - $Known 2>$null | Out-String).Trim()

Write-Host ''
Write-Host 'Installed.' -ForegroundColor Green
if ($auto -eq 'on') {
    Write-Host 'Updates arrive by themselves. (To turn that off: in Claude Code type /plugin, open Marketplaces, choose token-kit.)'
} elseif ($auto -eq 'user-off') {
    Write-Host 'Automatic updates stay off, as you set them. (To turn them on: in Claude Code type /plugin, open Marketplaces, choose token-kit.)'
} else {
    Write-Host 'Automatic updates could not be turned on. To turn them on: in Claude Code type /plugin, open Marketplaces, choose token-kit.' -ForegroundColor Yellow
}
Write-Host ''
Write-Host 'Next: close Claude Code completely (every window), open it again, and type:  /token-lab:token-lab'
}
