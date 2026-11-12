"""Garbage-collect finished H3TierExport experiment working directories.

Every batch run creates a new immutable directory under build/experiments and
nothing ever reclaims it, so the tree only grows (2026-10-31 measurement: 176
directories, 18.8 GiB, of which 9.7 GiB had been written within 24 hours and
4.1 GiB was still referenced from outside build/). This entry applies the same
disposition as retire_files.retire() — move into the user's review area with a
per-file SHA-256 receipt, never unlink — but into ONE batch for the whole sweep,
and it defaults to a dry run.

Gates for a directory to be collectable:
  1. its name is not referenced by any .json/.md/.py/.ps1/.txt file outside build/;
  2. no file inside it was written for at least --min-hours (default 24);
  3. it is not under the protected roots (creature-master-set holds the sanctioned
     scale candidates, per the repository art rules);
  4. its newest mtime has not changed between hashing and moving.

Usage:
  python scripts/gc_experiments.py                      # dry run, prints the plan
  python scripts/gc_experiments.py --apply --min-hours 24
"""
from __future__ import annotations

import argparse, datetime, hashlib, json, os, pathlib, re, sys

PRODUCT = pathlib.Path(__file__).resolve().parents[1]
EXPERIMENTS = PRODUCT / "build/experiments"
PROTECTED = ("creature-master-set",)
REVIEW_ROOT = pathlib.Path("{VX-root}/H3美术素材/待删除")
TEXT_SUFFIXES = {".json", ".md", ".py", ".ps1", ".txt", ".yaml", ".yml"}


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(8 * 1048576)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def reject_reparse(path):
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or getattr(candidate.stat(), "st_file_attributes", 0) & 0x400:
            raise RuntimeError("RETIRE_REPARSE_REJECTED: " + str(candidate))
        if candidate == PRODUCT:
            break


def external_reference_scan(candidate_names):
    """Return the candidate names mentioned by any text file outside build/."""
    hits = set()
    pending = set(candidate_names)
    for path in PRODUCT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if "build" in path.relative_to(PRODUCT).parts[:1]:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name in list(pending):
            if name in text:
                hits.add(name)
                pending.discard(name)
        if not pending:
            break
    return hits


def newest_mtime(directory):
    return max((f.stat().st_mtime for f in directory.rglob("*") if f.is_file()),
               default=directory.stat().st_mtime)


def plan(min_hours):
    candidates = [d for d in sorted(EXPERIMENTS.iterdir()) if d.is_dir()] if EXPERIMENTS.is_dir() else []
    candidates = [d for d in candidates if not any(d.name.startswith(p) for p in PROTECTED)]
    referenced = external_reference_scan([d.name for d in candidates])
    now = datetime.datetime.now().timestamp()
    rows = []
    for directory in candidates:
        newest = newest_mtime(directory)
        quiet_hours = (now - newest) / 3600
        size = sum(f.stat().st_size for f in directory.rglob("*") if f.is_file())
        files = sum(1 for f in directory.rglob("*") if f.is_file())
        rows.append({"name": directory.name, "bytes": size, "files": files,
                     "quiet_hours": round(quiet_hours, 1), "referenced_outside_build": directory.name in referenced,
                     "collectable": directory.name not in referenced and quiet_hours >= min_hours})
    rows.sort(key=lambda r: -r["bytes"])
    return rows


def apply(rows, batch_name):
    batch = REVIEW_ROOT / batch_name
    taken = [r for r in rows if r["collectable"]]
    if not taken:
        return {"moved": 0, "bytes": 0, "batch": str(batch)}
    batch.mkdir(parents=True, exist_ok=True)
    entries = []
    for row in taken:
        source = EXPERIMENTS / row["name"]
        target = batch / row["name"]
        reject_reparse(source)
        files = [f for f in source.rglob("*") if f.is_file()]
        files.sort()
        stamped = newest_mtime(source)
        per_file = []
        for f in files:
            if f.is_symlink() or getattr(f.stat(), "st_file_attributes", 0) & 0x400:
                raise RuntimeError("RETIRE_REPARSE_REJECTED: " + str(f))
            per_file.append({"source": str(f.resolve()),
                             "target": str((target / f.relative_to(source)).resolve()),
                             "bytes": f.stat().st_size, "sha256": sha256_file(f)})
        if newest_mtime(source) != stamped:
            entries.append({"name": row["name"], "status": "SKIPPED_CHANGED_DURING_HASHING"})
            continue
        os.rename(source, target)
        entries.append({"name": row["name"], "status": "MOVED_NOT_DELETED", "bytes": row["bytes"],
                        "files": len(per_file), "source": str(source.resolve()), "target": str(target.resolve()),
                        "per_file_sha256": per_file})
    receipt = {"schema": "h3.tool-retirement-batch.v1",
               "created_utc": datetime.datetime.now().isoformat(timespec="seconds"),
               "product": str(PRODUCT), "destination_batch": str(batch),
               "criteria": "unreferenced outside build/ AND quiet >= --min-hours AND not protected",
               "semantics": "MOVED_NOT_DELETED; same volume; restore by moving each directory back",
               "dirs": [{k: e[k] for k in ("name", "status", "bytes", "files", "source", "target") if k in e} for e in entries],
               "per_directory_files": {e["name"]: e.pop("per_file_sha256", []) for e in entries if "per_file_sha256" in e}}
    (batch / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"moved": sum(1 for e in entries if e["status"] == "MOVED_NOT_DELETED"),
            "bytes": sum(e.get("bytes", 0) for e in entries if e["status"] == "MOVED_NOT_DELETED"),
            "batch": str(batch), "receipt": str(batch / "receipt.json")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Move instead of printing the plan only.")
    parser.add_argument("--min-hours", type=float, default=24.0)
    parser.add_argument("--batch-name", default="H3TierExport-gc-experiments-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--top", type=int, default=15)
    args = parser.parse_args(argv)
    rows = plan(args.min_hours)
    collectable = [r for r in rows if r["collectable"]]
    print(json.dumps({"experiments_dirs": len(rows), "collectable_dirs": len(collectable),
                      "collectable_gib": round(sum(r["bytes"] for r in collectable) / 1073741824, 2),
                      "held_referenced_gib": round(sum(r["bytes"] for r in rows if r["referenced_outside_build"]) / 1073741824, 2),
                      "held_hot_gib": round(sum(r["bytes"] for r in rows if not r["referenced_outside_build"] and not r["collectable"]) / 1073741824, 2),
                      "protected_roots": list(PROTECTED), "mode": "APPLY" if args.apply else "DRY_RUN"},
                     ensure_ascii=False, indent=2))
    for row in collectable[:args.top]:
        print("  ", json.dumps({k: row[k] for k in ("name", "bytes", "quiet_hours")}, ensure_ascii=False))
    if not args.apply:
        return 0
    print(json.dumps(apply(rows, args.batch_name), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
