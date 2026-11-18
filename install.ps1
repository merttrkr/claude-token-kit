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
    Write-Host 'Claude Code bulunamadi. Once Claude Code kur: https://claude.com/claude-code' -ForegroundColor Red
    return
}

if (-not (Get-Command uvx -ErrorAction SilentlyContinue)) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host 'uv yok ve winget de yok. uv''yi elle kur: https://docs.astral.sh/uv/' -ForegroundColor Red
        return
    }
    Write-Host 'uv kuruluyor (winget)...'
    winget install --id astral-sh.uv -e --accept-package-agreements --accept-source-agreements --silent | Out-Null
    Refresh-Path
    if (-not (Get-Command uvx -ErrorAction SilentlyContinue)) {
        Write-Host 'uv kuruldu ama bu pencere goremiyor. Terminali kapatip acin ve komutu tekrar calistirin.' -ForegroundColor Yellow
        return
    }
}

$markets = (claude plugin marketplace list 2>&1) -join "`n"
if ($markets -match 'token-kit') {
    Write-Host 'token-kit marketplace zaten ekli; guncelleniyor...'
    claude plugin marketplace update token-kit | Out-Null
} else {
    claude plugin marketplace add $Repo | Out-Null
}

$plugins = (claude plugin list 2>&1) -join "`n"
if ($plugins -match 'token-lab@token-kit') {
    Write-Host 'token-lab zaten kurulu.'
} else {
    claude plugin install token-lab@token-kit | Out-Null
}

if (((claude plugin list 2>&1) -join "`n") -notmatch 'token-lab@token-kit') {
    Write-Host 'Kurulum dogrulanamadi: claude plugin list icinde token-lab yok.' -ForegroundColor Red
    return
}

Write-Host ''
Write-Host 'Kuruldu.' -ForegroundColor Green
Write-Host 'Claude Code''u yeniden ac (ya da acik oturumda /reload-plugins yaz), sonra:  /token-lab:token-lab'
