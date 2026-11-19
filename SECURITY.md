# Security

## Report a vulnerability

Report it privately: open <https://github.com/merttrkr/claude-token-kit/security> and choose **Report a vulnerability** (GitHub private vulnerability reporting).

Do not open a public issue for a vulnerability.

## What is in scope

- The install scripts (`install.ps1`, `install.sh`).
- Auto-update (the change the installers make to `known_marketplaces.json`).
- The report and `tokenlab inventory`, which read your local transcripts.
- The optimize skill, which changes files after you say yes.

## What the tool promises

- It reads local files only.
- It makes no network calls. The only download is uvx's first-run install.
- It never changes a file without your yes. The one exception: `tokenlab inventory` deletes its own leftover temporary folders (only its three files, older than 6 hours).

When you run it through the skill, Claude reads the report. So the report goes wherever your Claude conversation goes.

## Supported version

The latest release.
