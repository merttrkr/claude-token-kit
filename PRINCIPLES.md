# Principles

How this project is built and moved forward. Each principle has a rule, the reason, how it is checked, and its exceptions. A principle with no check is not applied yet; "planned" marks the checks still to build. `tests/test_release.py` checks that every test named here exists; the other checks (CI, PR questions, the rhythm) are kept by hand.

## Why we exist

Claude Code users pay for tokens they cannot see. This tool shows, from the user's own transcripts, where the tokens go and which should not be there, and helps remove them with the user's yes. It is for individual Claude Code users; one maintainer.

**North-star measure:** the user's fixed part per request or cuttable waste is lower four weeks after the first report; the Trend table shows it on the user's machine. Planned side measures: suggestions accepted, false alarms, time to the first useful finding. Every measure stays on the user's machine; sharing is the user's choice.

## 1. Trust and ethics first

- **Rule:** the tool sends nothing over the network. It writes the user's files only after the user says yes: to each diff, or once, up front, to the mode that applies every change and shows all diffs at the end (every file backed up first, each change listed with its backup); its own writes (history, temp folders) are listed in [SECURITY.md](SECURITY.md). Every number says where it comes from and what it leaves out. Transcripts hold other people's words (colleagues, customers): read the least, never copy transcript text into a report, an issue, a test or this repo.
- **Why:** the tool reads the most private data a developer has. One wrong number or one unasked write ends the trust.
- **Check:** `tests/conftest.py::_no_network`, `_no_home_history`; `tests/test_why.py::test_each_run_adds_its_numbers_to_the_history` (numbers only); the skills' diff-and-yes steps and the optimize mode question. PR question: "Does this change what is read, written or sent?" Planned: project exclusion and a consent note before optimize reads transcripts.
- **Exception:** none.

## 2. Measure the user's outcome, not the features

- **Rule:** a change is worth making if a number the user sees can show it helped. Name that number before building.
- **Why:** a tool about waste must not add waste; features that move no number are maintenance cost.
- **Check:** the Trend table and `--until`. PR question: "Which number will show this helped?"
- **Exception:** fixes for safety and correctness.

## 3. Data health before advice

- **Rule:** the report says what it could not read and how much of Claude Code's own cost counter the transcripts hold. When more than 1% of records could not be read or are of unknown types, it warns at the top. Planned: above 5% it gives a diagnosis only, no advice.
- **Why:** advice from incomplete data can make the user cut what they need.
- **Check:** `tests/test_why.py::test_records_it_cannot_read_are_counted_and_warned_about`, `test_the_report_says_how_much_of_claude_codes_counter_it_holds`. Planned: a test that unhealthy data gives no advice.
- **Exception:** none.

## 4. Defend against upstream change

- **Rule:** the transcript format is Claude Code's private format and changes without notice. Unknown record types are counted and shown; prices name their source; every new Claude Code version is checked against a fresh transcript once a month.
- **Why:** a silent format change produces confident, wrong numbers.
- **Check:** the health line (`KNOWN_TYPES` in `src/tokenlab/ingest.py`); golden tests in `tests/golden/`; the monthly item in the working rhythm below. Planned: the price catalog version in the report; an unknown model's cost shown as "not computed", never as zero.
- **Exception:** none.

## 5. Small, reversible releases

- **Rule:** auto-update ships a release within minutes to every user who has it on, so a release carries one user-facing topic (docs and process files may ride along), every new write can be turned off with a flag, tags are annotated (from v0.2.3; earlier ones are lightweight), and the CHANGELOG says what changed. To undo a release, publish the previous code as a new patch version. Tags are never moved: the runbook never force-pushes a tag, and `main` cannot be force-pushed.
- **Why:** a bad release reaches everyone before anyone reports it.
- **Check:** CI on Windows, macOS and Linux; `tests/test_release.py` (versions, README tag pin, the installers' auto-update block). PR question: "How do we undo it?" Planned: a canary step (the maintainer's machine first, everyone a day later) and a release script with `--dry-run`.
- **Exception:** a security fix may skip the canary wait.

## 6. Simplicity is a budget

- **Rule:** the supported surface is `why`, `inventory` and the two skills. A new concept the user must learn needs a reason in its decision note, and ideally replaces one. Research commands stay unsupported.
- **Why:** one maintainer; every concept is read, tested and explained forever.
- **Check:** PR question: "What does the user have to learn now?" The supported list in [CONTRIBUTING.md](CONTRIBUTING.md).
- **Exception:** none.

## 7. Decide with evidence, write it down

- **Rule:** work that changes behavior starts with a short decision note in `docs/decisions/`: the problem, the evidence, the options, the decision, how to undo it. Thresholds cite the data they came from.
- **Why:** the next maintainer (or the same one in six months) must know why a number is 5,000 and not 2,000.
- **Check:** the PR template asks for the note; the first one is [docs/decisions/001-trend-history.md](docs/decisions/001-trend-history.md). An independent read (Codex) reviews every change against this file.
- **Exception:** typos, docs, dependency bumps.

## 8. Survive without the maintainer

- **Rule:** [CONTRIBUTING.md](CONTRIBUTING.md) is enough to build, test and release. Account access (GitHub, the marketplace repo) is recoverable. If the project stops, the README says so and the repo is archived; users are told how to uninstall.
- **Why:** users run this on their machines with auto-update; an abandoned tool is a risk to them.
- **Check:** a release rehearsal from a fresh clone once a quarter. Planned: a second person with release rights.
- **Exception:** none.

## What we do not do

- No telemetry, and no network calls from the supported commands or the skills. (The research commands `probe` and `otel-sink` run `claude` and listen on a local port; they are not supported for users.)
- Nothing left running on the user's machine: no hook, no background program, no scheduled task.
- No write to a user's file without a yes: to its diff, or to the apply-all mode, which backs up every file and shows every diff at the end.
- No support for the research commands.
- No transcript text, personal data, company data or real paths in this repo, its tests or its issues.

## Working rhythm

- **Each change:** decision note (if it changes behavior) → small change with tests → independent read against this file → CI green → release with a CHANGELOG entry → look at the Trend a week later.
- **Each week:** read issues; pick the largest user pain, scored by how often it happens, its token effect, how sure the evidence is, its privacy risk and its maintenance cost.
- **Each month:** check the newest Claude Code version against a fresh transcript, and on the maintainer's machine re-measure with OpenTelemetry what never reaches a transcript (titles, suggestions, hook prompts, compaction), then update the coverage sentence in the report if the split changed (an OTel mode for users waits for a decision note and for users asking); talk to 3–5 users (what they understood, applied, skipped and why); review false alarms.
- **Each quarter:** a release rehearsal from a fresh clone; check account recovery.
