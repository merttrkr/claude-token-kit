#!/bin/sh
# token-lab install (macOS, Linux). One command:
#   curl -fsSL https://raw.githubusercontent.com/merttrkr/claude-token-kit/main/install.sh | sh
# What it does: checks for claude, installs uv if missing, adds and installs the plugin,
# turns on auto-update for token-kit (the field the /plugin > Marketplaces toggle writes).
# Updates what is already installed; touches no other setting.
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

# Auto-update: the same field the /plugin toggle writes, on the token-kit entry only.
KNOWN="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/known_marketplaces.json"
AUTO=$(uv run --no-project --quiet python - "$KNOWN" 2>/dev/null <<'EOF' || true
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
EOF
)

echo
echo "Installed. / Kuruldu."
if [ "$AUTO" = on ]; then
    echo "Auto-update is on; to turn it off: /plugin > Marketplaces > token-kit. / Otomatik guncelleme acik; kapatmak icin: /plugin > Marketplaces > token-kit."
elif [ "$AUTO" = user-off ]; then
    echo "Auto-update stays off, as you set it; to turn it on: /plugin > Marketplaces > token-kit. / Otomatik guncelleme sizin ayarinizla kapali kaliyor; acmak icin: /plugin > Marketplaces > token-kit."
else
    echo "Could not turn on auto-update; turn it on in /plugin > Marketplaces > token-kit. / Otomatik guncelleme acilamadi; /plugin > Marketplaces > token-kit icinden acin."
fi
echo "Restart Claude Code (or type /reload-plugins in an open session), then / Claude Code'u yeniden ac, sonra:  /token-lab:token-lab"
