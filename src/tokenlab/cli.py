import argparse
import hashlib
import importlib.metadata
import json
import math
import sqlite3
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tokenlab import (
    agents,
    budget,
    episodes,
    inventory,
    ledger,
    otel_sink,
    probe,
    reconcile,
    report,
    rules,
    simulate,
    why,
)
from tokenlab.ingest import ingest
from tokenlab.snapshot import snapshot

DEFAULT_DB = Path("data/ledger.sqlite")
PROBE_LOG = Path("data/probes/probes.jsonl")
# MCP servers connect asynchronously, so only an MCP-free config is deterministic.
PROBE_BASELINE = "base-nomcp"
PROJECTS = Path.home() / ".claude" / "projects"


INVENTORY_FILES = frozenset({"inventory.md", "pieces.md", "system-prompt.md"})
STALE_INVENTORY_S = 6 * 3600


def prune_inventories(tmp: Path, now: float) -> None:
    """Delete earlier inventory folders an interrupted optimize left behind: they
    hold the system prompt and file paths. Only folders with our files and nothing
    else, older than a few hours (another session may still be reading one)."""
    for d in tmp.glob("token-lab-*"):
        try:
            files = list(d.iterdir())
            if (
                d.is_symlink()
                or now - d.stat().st_mtime <= STALE_INVENTORY_S
                or any(
                    f.name not in INVENTORY_FILES or f.is_symlink() or not f.is_file()
                    for f in files
                )
            ):
                continue
            for f in files:
                f.unlink()
            d.rmdir()  # fails, and keeps the folder, if anything new is in it
        except OSError:
            continue


HISTORY = Path.home() / ".claude" / "token-lab" / "history.jsonl"
VERSION = importlib.metadata.version("tokenlab")
SEED_DAYS = 14  # the first run also measures this far back, for a trend
MAX_RUNS = 400  # history lines kept; older ones are dropped


def seed_cutoff(conn: sqlite3.Connection) -> str | None:
    """Where the first run's baseline ends: two weeks back, or halfway through a
    shorter history; None under two days of history."""
    first, last = conn.execute(
        "SELECT MIN(ts_first), MAX(ts_first) FROM requests"
    ).fetchone()
    if not first:
        return None
    lo, hi = why.when(first), why.when(last)
    if hi - lo < timedelta(days=2):
        return None
    back = max(hi - timedelta(days=SEED_DAYS), lo + (hi - lo) / 2)
    return back.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _number(v: object, lo: float, hi: float) -> bool:
    """A finite number in [lo, hi]; JSON booleans are not numbers here."""
    return (
        isinstance(v, int | float)
        and not isinstance(v, bool)
        and math.isfinite(v)
        and lo <= v <= hi
    )


def history_root(root: Path) -> str:
    """Which transcripts a run measured, without naming them: runs over another
    folder are not compared."""
    return hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]


def load_history(path: Path, root: str = "") -> list[dict[str, Any]]:
    """Earlier runs' numbers for the same transcripts, oldest first; damaged
    or implausible lines are skipped."""
    out: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            h = json.loads(line)
            why.when(h["end"])
            ok = (
                _number(h["fixed_per_request"], 0, float("inf"))
                and _number(h["cut_share"], 0, 1)
                and isinstance(h["angles"], dict)
                and all(_number(v, 0, 1) for v in h["angles"].values())
                and _number(h.get("days", 1), 1, 3650)
                and h.get("root", root) == root
            )
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
        if ok:
            out.append(h)
    return sorted(out, key=lambda h: why.when(h["end"]))


def save_history(path: Path, runs: list[dict[str, Any]]) -> None:
    """Append runs' numbers; a report never fails because of its history."""
    if not runs:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        cut = (
            path.exists()
            and path.stat().st_size
            and not path.read_bytes().endswith(b"\n")
        )
        with path.open("a", encoding="utf-8") as fh:
            if cut:
                fh.write("\n")  # a half-written last line stays alone and is skipped
            for h in runs:
                fh.write(json.dumps(h, sort_keys=True) + "\n")
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        if len(lines) > MAX_RUNS:
            tmp = path.with_suffix(".tmp")
            tmp.write_text("".join(lines[-MAX_RUNS:]), encoding="utf-8")
            tmp.replace(path)
    except OSError:
        pass


def default_root() -> Path:
    corpora = sorted(Path("data").glob("corpus-*"))
    return corpora[-1] if corpora else Path("data/corpus")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tokenlab")
    sub = parser.add_subparsers(dest="command")

    p_ingest = sub.add_parser(
        "ingest", help="rebuild the ledger from a transcript corpus"
    )
    p_ingest.add_argument(
        "--root",
        type=Path,
        default=None,
        help="corpus root (default: newest data/corpus-*)",
    )
    p_ingest.add_argument("--db", type=Path, default=DEFAULT_DB)

    p_rec = sub.add_parser(
        "reconcile", help="compare the ledger with Claude Code's cost-state"
    )
    p_rec.add_argument("--db", type=Path, default=DEFAULT_DB)
    p_rec.add_argument(
        "--sessions", action="store_true", help="one line per process and model"
    )

    p_rep = sub.add_parser("report", help="attribution report over the OTel era")
    p_rep.add_argument("--db", type=Path, default=DEFAULT_DB)

    p_agents = sub.add_parser(
        "agents", help="agent placement report (placement/when-to-agent.md)"
    )
    p_agents.add_argument("--db", type=Path, default=DEFAULT_DB)
    p_agents.add_argument("--days", type=int, default=30)
    p_agents.add_argument(
        "--agents-dir", type=Path, default=Path.home() / ".claude" / "agents"
    )
    p_agents.add_argument(
        "--skills-dir", type=Path, default=Path.home() / ".claude" / "skills"
    )
    p_agents.add_argument(
        "--rules", type=Path, default=None, help="TOML overrides (tokenlab/rules.py)"
    )

    p_why = sub.add_parser(
        "why", help="token map: where prompt tokens go and which should not be there"
    )
    p_why.add_argument(
        "--root", type=Path, default=PROJECTS, help="transcripts (read only)"
    )
    p_why.add_argument(
        "--db", type=Path, default=None, help="use this ledger instead of --root"
    )
    p_why.add_argument("--days", type=int, default=30)
    p_why.add_argument(
        "--history",
        type=Path,
        default=HISTORY,
        help="where the numbers of each run are kept for the trend (numbers only)",
    )
    p_why.add_argument(
        "--no-history",
        action="store_true",
        help="do not read or write the history",
    )
    p_why.add_argument(
        "--until",
        default=None,
        help="the report as it was at this UTC time (a date means its start),"
        " to compare before and after a change",
    )
    p_why.add_argument("--rules", type=Path, default=None)
    p_why.add_argument(
        "--agents-dir", type=Path, default=Path.home() / ".claude" / "agents"
    )
    p_why.add_argument(
        "--skills-dir", type=Path, default=Path.home() / ".claude" / "skills"
    )

    p_inv = sub.add_parser(
        "inventory",
        help="what /token-lab:optimize reads: pieces, sure checks, what to add",
    )
    p_inv.add_argument("--root", type=Path, default=PROJECTS)
    p_inv.add_argument("--db", type=Path, default=None)
    p_inv.add_argument("--days", type=int, default=30)
    p_inv.add_argument("--rules", type=Path, default=None)
    p_inv.add_argument(
        "--agents-dir", type=Path, default=Path.home() / ".claude" / "agents"
    )
    p_inv.add_argument(
        "--skills-dir", type=Path, default=Path.home() / ".claude" / "skills"
    )
    p_inv.add_argument(
        "--plugins-dir", type=Path, default=Path.home() / ".claude" / "plugins"
    )
    p_inv.add_argument(
        "--out",
        type=Path,
        default=None,
        help="folder for the two files (default: new temp folder)",
    )

    p_eps = sub.add_parser(
        "episodes", help="episode table: main vs agents per human turn"
    )
    p_eps.add_argument("--db", type=Path, default=DEFAULT_DB)
    p_eps.add_argument("--days", type=int, default=30)
    eps_out = p_eps.add_mutually_exclusive_group()
    eps_out.add_argument(
        "--rows", action="store_true", help="one tab-separated row per episode"
    )
    eps_out.add_argument(
        "--edges", action="store_true", help="one tab-separated row per agent"
    )

    p_sim = sub.add_parser("simulate", help="replay the OTel era under other policies")
    p_sim.add_argument("--db", type=Path, default=DEFAULT_DB)
    p_sim.add_argument(
        "--since", default="", help="replay only requests after this UTC timestamp"
    )

    p_probe = sub.add_parser(
        "probe", help="run one trivial `claude -p` request per config; log its prompt"
    )
    p_probe.add_argument("label")
    p_probe.add_argument("--model", default="haiku")
    p_probe.add_argument("--repeat", type=int, default=1)
    p_probe.add_argument("--cwd", type=Path, default=Path("..") / "_tokenlab-probe")
    p_probe.add_argument("--log", type=Path, default=PROBE_LOG)

    p_probes = sub.add_parser("probes", help="compare logged probes with a baseline")
    p_probes.add_argument("--baseline", default=PROBE_BASELINE)
    p_probes.add_argument("--log", type=Path, default=PROBE_LOG)

    p_bud = sub.add_parser(
        "budget", help="live base-nomcp probe against the ceilings in budgets.toml"
    )
    p_bud.add_argument("--file", type=Path, default=Path("budgets.toml"))
    p_bud.add_argument("--log", type=Path, default=Path("data/probes/budget.jsonl"))

    p_otel = sub.add_parser(
        "otel-sink", help="receive OTLP/HTTP-JSON into a JSONL file"
    )
    p_otel.add_argument("--out", type=Path, default=Path("data/otel/otel.jsonl"))
    p_otel.add_argument("--port", type=int, default=4318)

    p_snap = sub.add_parser(
        "snapshot", help="freeze transcripts and OTel exports into a new corpus"
    )
    p_snap.add_argument(
        "--projects", type=Path, default=Path.home() / ".claude" / "projects"
    )
    p_snap.add_argument("--otel", type=Path, default=Path("data/otel"))
    p_snap.add_argument(
        "--dest", type=Path, default=None, help="default: data/corpus-<UTC date>"
    )

    argv = sys.argv[1:] if argv is None else argv
    # `tokenlab probe LABEL [options] -- <extra claude arguments>`
    cut = argv.index("--") if "--" in argv else len(argv)
    argv, extra = argv[:cut], argv[cut + 1 :]
    args = parser.parse_args(argv)
    if args.command == "snapshot":
        dest = args.dest or Path(f"data/corpus-{datetime.now(UTC):%Y-%m-%d}")
        n = snapshot(args.projects, args.otel, dest)
        print(f"snapshot {dest}  files={n}")
        return 0
    if args.command == "why":
        r = rules.load(args.rules)
        catalog = agents.load_catalog(args.agents_dir)
        refs = agents.skill_refs(args.skills_dir, sorted(catalog))

        keep = not args.no_history and not args.until and args.db is None
        here = history_root(args.root)
        history = load_history(args.history, here) if keep else []
        new: list[dict[str, Any]] = []

        def full_report(conn: sqlite3.Connection) -> str:
            now: dict[str, Any] = {}
            text = why.render(conn, args.days, r, catalog, history=history, out=now)
            if not now:
                return text  # no requests yet: nothing else to say
            new.append({**now, "root": here, "version": VERSION})
            return (
                text
                + "\n## 5. Agent work (detail)\n\n"
                + agents.render(conn, catalog, refs, args.days, r).split("\n", 1)[1]
            )

        if args.db is not None:
            if args.until:
                print("--until reads transcripts; it does not work with --db.")
                return 2
            sys.stdout.write(full_report(ledger.open_ledger(args.db)))
            return 0
        if not args.root.is_dir():
            print(
                f"No Claude Code history found in {args.root}. Use Claude Code for a"
                " few sessions, then run this again."
            )
            return 1
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "ledger.sqlite"
            ingest(args.root, db, args.until)
            conn = ledger.open_ledger(db)
            back = seed_cutoff(conn) if keep and not history else None
            if back:
                # First run: measure an earlier point once, so a trend shows today.
                seed_db = Path(tmp) / "seed.sqlite"
                ingest(args.root, seed_db, back)
                seed_conn = ledger.open_ledger(seed_db)
                try:
                    seed: dict[str, Any] = {}
                    why.render(seed_conn, args.days, r, catalog, out=seed)
                finally:
                    seed_conn.close()
                if seed:
                    seed = {**seed, "root": here, "version": VERSION}
                    history.append(seed)
                    new.append(seed)
            try:
                sys.stdout.write(full_report(conn))
            finally:
                conn.close()
        if keep:
            save_history(args.history, new)
        return 0
    if args.command == "inventory":
        if not args.root.is_dir():
            print(
                f"No Claude Code history found in {args.root}. Use Claude Code for a"
                " few sessions, then run this again."
            )
            return 1
        if args.out is None:
            prune_inventories(Path(tempfile.gettempdir()), time.time())
        out = args.out or Path(tempfile.mkdtemp(prefix="token-lab-"))
        out.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            db = args.db or Path(tmp) / "ledger.sqlite"
            if args.db is None or not args.db.exists():
                ingest(args.root, db)
            conn = ledger.open_ledger(db)
            try:
                text, pieces = inventory.render(
                    conn,
                    args.root,
                    args.agents_dir,
                    args.skills_dir,
                    args.plugins_dir,
                    args.days,
                    rules.load(args.rules),
                )
            finally:
                conn.close()
        (out / "inventory.md").write_text(text, encoding="utf-8")
        (out / "pieces.md").write_text(pieces, encoding="utf-8")
        (out / "system-prompt.md").write_text(
            inventory.system_prompt(args.root), encoding="utf-8"
        )
        print(out)
        return 0
    if args.command == "ingest":
        root = args.root or default_root()
        print(f"ledger {ingest(root, args.db)}  root={root}  db={args.db}")
        return 0
    if args.command == "reconcile":
        conn = ledger.open_ledger(args.db)
        rows = reconcile.rows(conn)
        sys.stdout.write(reconcile.render(rows, per_session=args.sessions))
        sys.stdout.write(reconcile.render_otel(conn))
        otel_ok = reconcile.otel_reconciles(reconcile.otel_rows(conn))
        return 0 if reconcile.output_reconciles(rows) and otel_ok is not False else 1
    if args.command == "report":
        sys.stdout.write(report.render(ledger.open_ledger(args.db)))
        return 0
    if args.command == "agents":
        catalog = agents.load_catalog(args.agents_dir)
        refs = agents.skill_refs(args.skills_dir, sorted(catalog))
        sys.stdout.write(
            agents.render(
                ledger.open_ledger(args.db),
                catalog,
                refs,
                args.days,
                rules.load(args.rules),
            )
        )
        return 0
    if args.command == "episodes":
        conn = ledger.open_ledger(args.db)
        render = (
            episodes.edges
            if args.edges
            else episodes.rows
            if args.rows
            else episodes.render
        )
        sys.stdout.write(render(conn, args.days))
        return 0
    if args.command == "simulate":
        trims = probe.trims(probe.load(PROBE_LOG), PROBE_BASELINE)
        sys.stdout.write(
            simulate.render(ledger.open_ledger(args.db), trims, args.since)
        )
        return 0
    if args.command == "probe":
        args.cwd.mkdir(parents=True, exist_ok=True)
        for _ in range(args.repeat):
            row = probe.run(args.label, args.model, extra, args.cwd, PROJECTS)
            probe.append(args.log, row)
            print(f"{row.label} {row.model} prompt={row.prompt:,} usd={row.cost_usd}")
        return 0
    if args.command == "probes":
        sys.stdout.write(probe.render(probe.load(args.log), args.baseline))
        return 0
    if args.command == "budget":
        failed = False
        for section, ceilings in budget.load(args.file).items():
            place = budget.SECTIONS[section]
            place.cwd.mkdir(parents=True, exist_ok=True)
            flags = ["--strict-mcp-config"]
            if not place.persist:
                flags.append("--no-session-persistence")
            row = probe.run(f"budget-{section}", "haiku", flags, place.cwd, PROJECTS)
            probe.append(args.log, row)
            over = budget.check(row, ceilings)
            failed = failed or bool(over)
            print(f"budget {section}: prompt {row.prompt:,} tokens, {len(over)} over")
            for line in over:
                print(f"  OVER {line}")
        return 1 if failed else 0
    if args.command == "otel-sink":
        otel_sink.serve(args.out, port=args.port)
        return 0
    parser.print_help()
    return 0
