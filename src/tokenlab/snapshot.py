"""Freeze transcripts and OTel exports into a new corpus.

A corpus is never modified once written, so each snapshot is a new dated folder.
`cleanupPeriodDays` is 3650, so a later snapshot is a superset of an earlier one.
"""

import hashlib
import shutil
from pathlib import Path

from tokenlab.otel import OTEL_DIR

MANIFEST = "MANIFEST.sha256"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(root: Path) -> int:
    files = sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink())
    lines = [
        f"{_sha256(p)}  ./{p.relative_to(root).as_posix()}"
        for p in files
        if p.relative_to(root).as_posix() != MANIFEST
    ]
    (root / MANIFEST).write_text("\n".join(lines) + "\n")
    return len(lines)


def snapshot(projects: Path, otel_dir: Path, dest: Path) -> int:
    """Copy `projects` and `otel_dir/*.jsonl` into `dest`, which must not exist.
    Returns the number of files in the manifest."""
    if dest.exists():
        raise FileExistsError(f"{dest} exists; corpora are never overwritten")
    shutil.copytree(projects, dest, symlinks=True)
    if (dest / OTEL_DIR).exists():
        raise ValueError(f"{projects} has a project named {OTEL_DIR}")
    (dest / OTEL_DIR).mkdir()
    for f in sorted(otel_dir.glob("*.jsonl")):
        shutil.copy2(f, dest / OTEL_DIR / f.name)
    return write_manifest(dest)
