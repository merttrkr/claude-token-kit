#!/bin/sh
# token-lab install (macOS, Linux). One command (vX.Y.Z = the latest release tag, see README):
#   curl -fsSL https://raw.githubusercontent.com/merttrkr/claude-token-kit/vX.Y.Z/install.sh | sh
# What it does: checks for claude, installs uv if missing, adds and installs the plugin,
# turns on auto-update for token-kit (the field the /plugin > Marketplaces toggle writes).
# Updates what is already installed; touches no other setting.
set -eu

REPO=https://github.com/merttrkr/claude-token-kit
PATH="$HOME/.local/bin:$PATH"  # where the uv installer puts uv

if ! command -v claude >/dev/null 2>&1; then
    echo "Claude Code not found. Install it first: https://claude.com/claude-code"
    exit 1
fi

if ! command -v uvx >/dev/null 2>&1; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
    if ! command -v uvx >/dev/null 2>&1; then
        echo "uv installed, but this shell cannot see it. Open a new terminal and run the command again."
        exit 1
    fi
fi

if claude plugin marketplace list 2>&1 | grep -q token-kit; then
    echo "token-kit marketplace already added; updating..."
    claude plugin marketplace update token-kit >/dev/null
else
    claude plugin marketplace add "$REPO" >/dev/null
fi

if claude plugin list 2>&1 | grep -q 'token-lab@token-kit'; then
    echo "token-lab already installed; updating..."
    claude plugin update token-lab@token-kit >/dev/null
else
    claude plugin install token-lab@token-kit >/dev/null
fi

if ! claude plugin list 2>&1 | grep -q 'token-lab@token-kit'; then
    echo "Install could not be verified: token-lab is not in claude plugin list."
    exit 1
fi

# An update that failed leaves the old version listed: compare with the marketplace.
CONFIG="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
MANIFEST="$CONFIG/plugins/marketplaces/token-kit/.claude-plugin/marketplace.json"
PLUGINS="$(claude plugin list --json 2>/dev/null || true)"
export PLUGINS
VERSIONS=$(uv run --no-project --quiet python - "$MANIFEST" 2>/dev/null <<'EOF' || true
import json, os, sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        latest = next(p["version"] for p in json.load(f)["plugins"] if p["name"] == "token-lab")
    plugins = json.loads(os.environ["PLUGINS"])
    installed = next(p["version"] for p in plugins if p["id"] == "token-lab@token-kit")
    print(installed, latest)
except Exception:
    pass
EOF
)
set -- $VERSIONS
if [ $# -eq 2 ] && [ "$1" != "$2" ]; then
    echo "Update did not finish: version $1 is installed, $2 is the latest. Run: claude plugin update token-lab@token-kit"
    exit 1
fi

# Auto-update: the same field the /plugin toggle writes, on the token-kit entry only.
KNOWN="$CONFIG/plugins/known_marketplaces.json"
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
echo "Installed."
if [ "$AUTO" = on ]; then
    echo "Auto-update is on; to turn it off: /plugin > Marketplaces > token-kit."
elif [ "$AUTO" = user-off ]; then
    echo "Auto-update stays off, as you set it; to turn it on: /plugin > Marketplaces > token-kit."
else
    echo "Could not turn on auto-update; turn it on in /plugin > Marketplaces > token-kit."
fi
echo "Restart Claude Code (or type /reload-plugins in an open session), then type /token-lab:token-lab"
