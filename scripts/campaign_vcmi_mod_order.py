"""Stage and safely apply VCMI mod load-order metadata for campaign assets.

Only reads/writes VCMI mod metadata; source code and manifests stay in H3TierExport.
The apply command refuses while VCMI is running and archives the old mod.json.
"""
from __future__ import annotations
import argparse, hashlib, json, os, subprocess, sys
from pathlib import Path


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path: Path) -> tuple[dict, bytes]:
    raw = Path(path).read_bytes()
    doc = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(doc, dict):
        raise ValueError("expected JSON object: " + str(path))
    return doc, raw


def write_new(path: Path, raw: bytes) -> None:
    path = Path(path)
    if path.exists():
        raise FileExistsError("refusing overwrite: " + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def prepare(config_path: Path) -> dict:
    config, _ = read_json(config_path)
    mods_root = Path(config["vcmi_root"]).resolve() / "Mods"
    mod_root = mods_root / config["mod_id"]
    target = mod_root / "mod.json"
    depends = [str(x) for x in config.get("soft_depends", [])]
    if not depends or len(set(depends)) != len(depends):
        raise ValueError("soft dependency list must be nonempty and unique")
    for dep in depends:
        if not (mods_root / dep / "mod.json").is_file():
            raise FileNotFoundError("soft dependency mod is not installed: " + dep)
    current, current_raw = read_json(target)
    current_sha = hashlib.sha256(current_raw).hexdigest()
    expected = config.get("expected_current_mod_json_sha256")
    if expected and current_sha != expected:
        raise ValueError("installed mod.json changed since configuration was frozen")
    prior = current.get("softDepends")
    if prior is not None and prior != depends:
        raise ValueError("existing softDepends differs; manual review required")
    candidate = dict(current)
    candidate["softDepends"] = depends
    candidate_raw = (json.dumps(candidate, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    candidate_path = Path(config["candidate_mod_json"]).absolute()
    write_new(candidate_path, candidate_raw)
    # Record the concrete Data3x collisions that the dependency ordering resolves.
    collisions = []
    for dep in depends:
        other = mods_root / dep / "content" / "Data3x"
        our_data = mod_root / "content" / "Data3x"
        if other.is_dir() and our_data.is_dir():
            for p in our_data.glob("*.png"):
                q = other / p.name
                if q.is_file() and sha(p) != sha(q):
                    collisions.append({"filename": p.name, "other_mod": dep,
                                       "campaign_sha256": sha(p), "prior_sha256": sha(q)})
    receipt = {
        "schema": "h3.campaign-vcmi-mod-load-order-candidate.v1",
        "operation_id": config["operation_id"],
        "status": "LOAD_ORDER_CANDIDATE_READY",
        "mod_id": config["mod_id"], "mod_root": str(mod_root),
        "current_mod_json_sha256": current_sha,
        "candidate_mod_json": str(candidate_path),
        "candidate_mod_json_sha256": hashlib.sha256(candidate_raw).hexdigest(),
        "softDepends": depends, "conflicts_resolved_by_load_order": collisions,
        "schema_property_verified_from": str(Path(config["vcmi_root"]) / "config" / "schemas" / "mod.json"),
        "install_receipt_path": config["install_receipt_path"],
    }
    receipt_path = Path(config["prepare_receipt_path"])
    write_new(receipt_path, (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return receipt


def running_vcmi(vcmi_root: Path) -> list[str]:
    query = ("$names=@('VCMI_client.exe','VCMI_server.exe','VCMI_launcher.exe'); "
             "Get-CimInstance Win32_Process -ErrorAction Stop | "
             "Where-Object { $names -contains $_.Name } | "
             "Select-Object Name,ExecutablePath,CommandLine | ConvertTo-Json -Compress")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", query],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        raise RuntimeError("could not determine VCMI idle state")
    raw = result.stdout.strip()
    if not raw:
        return []
    values = json.loads(raw)
    if isinstance(values, dict): values = [values]
    root = vcmi_root.resolve(); root_text = str(root).casefold(); active = []
    for row in values:
        exe = str(row.get("ExecutablePath") or "")
        cmd = str(row.get("CommandLine") or "").casefold()
        try:
            if (exe and Path(exe).resolve().is_relative_to(root)) or root_text in cmd:
                active.append(str(row.get("Name") or exe))
        except OSError:
            active.append(str(row.get("Name") or "VCMI process") + " (path unresolved)")
    return sorted(set(active))


def apply(config_path: Path) -> dict:
    config, _ = read_json(config_path)
    mods_root = Path(config["vcmi_root"]).resolve() / "Mods"
    mod_root = mods_root / config["mod_id"]
    target = mod_root / "mod.json"
    prep_path = Path(config["prepare_receipt_path"])
    prep, _ = read_json(prep_path)
    candidate_path = Path(prep["candidate_mod_json"])
    candidate_raw = candidate_path.read_bytes()
    if hashlib.sha256(candidate_raw).hexdigest() != prep["candidate_mod_json_sha256"]:
        raise ValueError("load-order candidate SHA mismatch")
    current_raw = target.read_bytes()
    if hashlib.sha256(current_raw).hexdigest() != prep["current_mod_json_sha256"]:
        raise ValueError("installed mod.json changed since prepare")
    receipt_out = Path(config["install_receipt_path"])
    if receipt_out.exists(): raise FileExistsError("install receipt exists: " + str(receipt_out))
    active = running_vcmi(Path(config["vcmi_root"]))
    if active: raise RuntimeError("VCMI_UPDATE_BLOCKED_GAME_RUNNING: " + ", ".join(active))
    archive_root = Path(config["archive_root"]).resolve()
    batch = archive_root / config["operation_id"]
    archived = batch / "previous-mod" / config["mod_id"] / "mod.json"
    movement = batch / "movement-manifest.json"
    if batch.exists(): raise FileExistsError("mod metadata archive exists: " + str(batch))
    batch.mkdir(parents=True, exist_ok=False)
    movement_doc = {
        "schema": "h3.art-mod-replacement-movement.v1",
        "operation_id": config["operation_id"], "status": "ARCHIVE_INTENT",
        "entries": [{"original_path": str(target), "archived_path": str(archived),
                     "sha256": prep["current_mod_json_sha256"],
                     "reason": "Add VCMI soft dependency so campaign SP_BG overrides h3hd-maptown",
                     "result": "ARCHIVE_INTENT"}],
    }
    write_new(movement, (json.dumps(movement_doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    archived.parent.mkdir(parents=True, exist_ok=True)
    stage = mod_root / ("mod.json.partial-" + config["operation_id"])
    if stage.exists(): raise FileExistsError("partial mod.json exists: " + str(stage))
    write_new(stage, candidate_raw)
    os.rename(target, archived)
    if sha(archived) != prep["current_mod_json_sha256"]:
        raise RuntimeError("archived old mod.json SHA mismatch")
    os.rename(stage, target)
    if sha(target) != prep["candidate_mod_json_sha256"]:
        raise RuntimeError("new mod.json readback mismatch")
    movement_result = batch / "movement-result.json"
    write_new(movement_result, (json.dumps({
        "schema": "h3.art-mod-replacement-movement-result.v1",
        "operation_id": config["operation_id"], "status": "MOVED_NOT_DELETED",
        "original_path": str(target), "archived_path": str(archived),
        "sha256": prep["current_mod_json_sha256"], "result": "MOVED_NOT_DELETED",
    }, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    result = {
        "schema": "h3.campaign-vcmi-mod-load-order-install.v1",
        "operation_id": config["operation_id"], "status": "MOD_JSON_LOAD_ORDER_UPDATED_SHA_READBACK_PASS",
        "mod_id": config["mod_id"], "mod_json": str(target),
        "old_sha256": prep["current_mod_json_sha256"],
        "new_sha256": prep["candidate_mod_json_sha256"], "softDepends": prep["softDepends"],
        "resolved_conflicts": prep["conflicts_resolved_by_load_order"],
        "archived_old_mod_json": str(archived), "movement_manifest": str(movement),
        "movement_result": str(movement_result), "runtime_accepted": False,
    }
    write_new(receipt_out, (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("prepare", "apply"):
        p = sub.add_parser(name); p.add_argument("--config", type=Path, required=True)
    args = ap.parse_args()
    try:
        result = prepare(args.config) if args.command == "prepare" else apply(args.config)
        print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, FileExistsError) as error:
        print("VCMI_LOAD_ORDER_REFUSED: " + str(error), file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
