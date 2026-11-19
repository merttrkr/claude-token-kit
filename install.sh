#!/bin/sh
# token-lab install (macOS, Linux). One command (vX.Y.Z = the latest release tag, see README):
#   curl -fsSL https://raw.githubusercontent.com/merttrkr/claude-token-kit/vX.Y.Z/install.sh | sh
# What it does: checks for claude, installs uv if missing, adds and installs the plugin,
# turns on auto-update for token-kit (the field the /plugin > Marketplaces toggle writes).
# Updates what is already installed; touches no other setting.
set -eu

REPO=https://github.com/merttrkr/claude-token-kit
PATH="$HOME/.local/bin:$PATH"  # where the uv installer puts uv

# Runs `claude <args>`; on failure prints what claude said, so the user sees why.
run_claude() {
    if ! said=$(claude "$@" 2>&1); then
        [ -n "$said" ] && printf '%s\n' "$said"
        return 1
    fi
}

if ! command -v claude >/dev/null 2>&1; then
    echo "Claude Code is not installed (or this window cannot find it)."
    echo "Install it from https://claude.com/claude-code, open a new Terminal window, and paste the install command again."
    exit 1
fi

if ! command -v uvx >/dev/null 2>&1; then
    echo "Installing uv, a small tool token-lab runs on (about a minute)..."
    if ! uv_script=$(curl -LsSf https://astral.sh/uv/install.sh); then
        echo "Could not download uv. Check the internet connection and paste the install command again."
        exit 1
    fi
    if ! printf '%s\n' "$uv_script" | sh >/dev/null; then
        echo "Could not install uv (message above). Install it from https://docs.astral.sh/uv/getting-started/installation/ and paste the install command again."
        exit 1
    fi
    if ! command -v uvx >/dev/null 2>&1; then
        echo "uv is installed, but this window cannot see it yet."
        echo "Close this Terminal window, open a new one, and paste the install command again."
        exit 1
    fi
fi

echo "Adding token-lab to Claude Code..."
if claude plugin marketplace list 2>&1 | grep -q token-kit; then
    ok=0; run_claude plugin marketplace update token-kit || ok=1
else
    ok=0; run_claude plugin marketplace add "$REPO" || ok=1
fi
if [ "$ok" -ne 0 ]; then
    echo "Could not reach the token-lab catalog on GitHub (message above). Check the internet connection and paste the install command again."
    exit 1
fi

if claude plugin list 2>&1 | grep -q 'token-lab@token-kit'; then
    ok=0; run_claude plugin update token-lab@token-kit || ok=1
else
    ok=0; run_claude plugin install token-lab@token-kit || ok=1
fi
if [ "$ok" -ne 0 ]; then
    echo "Could not install token-lab (message above). Paste the install command again; if it fails twice, open an issue on GitHub."
    exit 1
fi

if ! claude plugin list 2>&1 | grep -q 'token-lab@token-kit'; then
    echo "Install could not be confirmed: Claude Code does not list token-lab. Paste the install command again."
    exit 1
fi

echo "Checking the version and turning on automatic updates (the first time this may download Python; up to a minute)..."
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
    echo "The update did not finish: version $1 is installed, $2 is the latest."
    echo "Paste the install command again in this window."
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
    echo "Updates arrive by themselves. (To turn that off: in Claude Code type /plugin, open Marketplaces, choose token-kit.)"
elif [ "$AUTO" = user-off ]; then
    echo "Automatic updates stay off, as you set them. (To turn them on: in Claude Code type /plugin, open Marketplaces, choose token-kit.)"
else
    echo "Automatic updates could not be turned on. To turn them on: in Claude Code type /plugin, open Marketplaces, choose token-kit."
fi
echo
echo "Next: close Claude Code completely (every window), open it again, and type:  /token-lab:token-lab"
