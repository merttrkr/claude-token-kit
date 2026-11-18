#!/bin/sh
# token-lab install (macOS, Linux). One command:
#   curl -fsSL https://raw.githubusercontent.com/merttrkr/claude-token-kit/main/install.sh | sh
# What it does: checks for claude, installs uv if missing, adds and installs the plugin.
# Skips what is already installed; touches no other setting.
set -eu

REPO=https://github.com/merttrkr/claude-token-kit
PATH="$HOME/.local/bin:$PATH"  # where the uv installer puts uv

if ! command -v claude >/dev/null 2>&1; then
    echo "Claude Code not found. Install it first / Claude Code bulunamadi, once onu kur: https://claude.com/claude-code"
    exit 1
fi

if ! command -v uvx >/dev/null 2>&1; then
    echo "Installing uv / uv kuruluyor..."
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
    if ! command -v uvx >/dev/null 2>&1; then
        echo "uv installed, but this shell cannot see it. Open a new terminal and run the command again. / uv kuruldu ama bu terminal goremiyor; yeni terminal acip komutu tekrar calistirin."
        exit 1
    fi
fi

if claude plugin marketplace list 2>&1 | grep -q token-kit; then
    echo "token-kit marketplace already added; updating / zaten ekli, guncelleniyor..."
    claude plugin marketplace update token-kit >/dev/null
else
    claude plugin marketplace add "$REPO" >/dev/null
fi

if claude plugin list 2>&1 | grep -q 'token-lab@token-kit'; then
    echo "token-lab already installed; updating / zaten kurulu, guncelleniyor..."
    claude plugin update token-lab@token-kit >/dev/null
else
    claude plugin install token-lab@token-kit >/dev/null
fi

if ! claude plugin list 2>&1 | grep -q 'token-lab@token-kit'; then
    echo "Install could not be verified: token-lab is not in claude plugin list. / Kurulum dogrulanamadi."
    exit 1
fi

echo
echo "Installed. / Kuruldu."
echo "Restart Claude Code (or type /reload-plugins in an open session), then / Claude Code'u yeniden ac, sonra:  /token-lab:token-lab"
