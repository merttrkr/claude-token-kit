import argparse
import sqlite3
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from tokenlab import (
    agents,
    budget,
    episodes,
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
    p_why.add_argument("--rules", type=Path, default=None)
    p_why.add_argument(
        "--agents-dir", type=Path, default=Path.home() / ".claude" / "agents"
    )
    p_why.add_argument(
        "--skills-dir", type=Path, default=Path.home() / ".claude" / "skills"
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

        def full_report(conn: sqlite3.Connection) -> str:
            return (
                why.render(conn, args.days, r)
                + "\n## 5. Ajan işleri (ayrıntı, İngilizce)\n\n"
                + agents.render(conn, catalog, refs, args.days, r).split("\n", 1)[1]
            )

        if args.db is not None:
            sys.stdout.write(full_report(ledger.open_ledger(args.db)))
            return 0
        if not args.root.is_dir():
            print(f"Transcript klasörü yok: {args.root}. Claude Code'u bir kez kullan.")
            return 1
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "ledger.sqlite"
            ingest(args.root, db)
            conn = ledger.open_ledger(db)
            try:
                sys.stdout.write(full_report(conn))
            finally:
                conn.close()
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
