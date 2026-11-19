# token-lab kurulumu (Windows). Tek komut:
#   irm https://raw.githubusercontent.com/merttrkr/claude-token-kit/main/install.ps1 | iex
# Yaptiklari: claude var mi bakar, uv yoksa winget ile kurar, plugin'i ekler ve kurar,
# token-kit icin otomatik guncellemeyi acar (/plugin > Marketplaces'teki dugmenin yazdigi alan).
# Zaten kurulu olani gunceller; baska hicbir ayara dokunmaz.

$ErrorActionPreference = 'Stop'
$Repo = 'https://github.com/merttrkr/claude-token-kit'

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
        [Environment]::GetEnvironmentVariable('Path', 'User')
}

if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Host 'Claude Code not found. Install it first / Claude Code bulunamadi, once onu kur: https://claude.com/claude-code' -ForegroundColor Red
    return
}

if (-not (Get-Command uvx -ErrorAction SilentlyContinue)) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host 'No uv and no winget. Install uv by hand / uv ve winget yok, uv''yi elle kur: https://docs.astral.sh/uv/' -ForegroundColor Red
        return
    }
    Write-Host 'Installing uv (winget) / uv kuruluyor...'
    winget install --id astral-sh.uv -e --accept-package-agreements --accept-source-agreements --silent | Out-Null
    Refresh-Path
    if (-not (Get-Command uvx -ErrorAction SilentlyContinue)) {
        Write-Host 'uv installed, but this window cannot see it. Reopen the terminal and run the command again. / uv kuruldu ama bu pencere goremiyor; terminali kapatip acin ve komutu tekrar calistirin.' -ForegroundColor Yellow
        return
    }
}

$markets = (claude plugin marketplace list 2>&1) -join "`n"
if ($markets -match 'token-kit') {
    Write-Host 'token-kit marketplace already added; updating / zaten ekli, guncelleniyor...'
    claude plugin marketplace update token-kit | Out-Null
} else {
    claude plugin marketplace add $Repo | Out-Null
}

$plugins = (claude plugin list 2>&1) -join "`n"
if ($plugins -match 'token-lab@token-kit') {
    Write-Host 'token-lab already installed; updating / zaten kurulu, guncelleniyor...'
    claude plugin update token-lab@token-kit | Out-Null
} else {
    claude plugin install token-lab@token-kit | Out-Null
}

if (((claude plugin list 2>&1) -join "`n") -notmatch 'token-lab@token-kit') {
    Write-Host 'Install could not be verified: token-lab is not in claude plugin list. / Kurulum dogrulanamadi.' -ForegroundColor Red
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
$ConfigDir = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $HOME '.claude' }
$Known = Join-Path $ConfigDir 'plugins\known_marketplaces.json'
$auto = & {
    # Child scope: under irm | iex this script runs in the caller's session.
    $ErrorActionPreference = 'Continue'  # Windows PowerShell turns native stderr into errors
    ($AutoUpdate | uv run --no-project --quiet python - $Known 2>$null) -join ''
}

Write-Host ''
Write-Host 'Installed. / Kuruldu.' -ForegroundColor Green
if ($auto -eq 'on') {
    Write-Host 'Auto-update is on; to turn it off: /plugin > Marketplaces > token-kit. / Otomatik guncelleme acik; kapatmak icin: /plugin > Marketplaces > token-kit.'
} elseif ($auto -eq 'user-off') {
    Write-Host 'Auto-update stays off, as you set it; to turn it on: /plugin > Marketplaces > token-kit. / Otomatik guncelleme sizin ayarinizla kapali kaliyor; acmak icin: /plugin > Marketplaces > token-kit.'
} else {
    Write-Host 'Could not turn on auto-update; turn it on in /plugin > Marketplaces > token-kit. / Otomatik guncelleme acilamadi; /plugin > Marketplaces > token-kit icinden acin.' -ForegroundColor Yellow
}
Write-Host 'Restart Claude Code (or type /reload-plugins in an open session), then / Claude Code''u yeniden ac, sonra:  /token-lab:token-lab'
