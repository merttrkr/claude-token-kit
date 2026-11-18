# token-lab kurulumu (Windows). Tek komut:
#   irm https://raw.githubusercontent.com/merttrkr/claude-token-kit/main/install.ps1 | iex
# Yaptiklari: claude var mi bakar, uv yoksa winget ile kurar, plugin'i ekler ve kurar.
# Zaten kurulu olani atlar; baska hicbir ayara dokunmaz.

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

Write-Host ''
Write-Host 'Installed. / Kuruldu.' -ForegroundColor Green
Write-Host 'Restart Claude Code (or type /reload-plugins in an open session), then / Claude Code''u yeniden ac, sonra:  /token-lab:token-lab'
