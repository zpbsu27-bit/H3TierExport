"""Move retired tool files into recoverable batches instead of deleting them."""
from pathlib import Path
import datetime
import hashlib
import json
import os
import uuid

PRODUCT = Path(__file__).resolve().parents[1]


def retire(path, product=PRODUCT, reason="superseded tool file", batch=None):
    product = Path(product).absolute()
    path = Path(path).absolute()
    relative = path.relative_to(product)
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or getattr(candidate.stat(), "st_file_attributes", 0) & 0x400:
            raise RuntimeError("RETIRE_REPARSE_REJECTED: " + str(candidate))
        if candidate == product:
            break
    production = product == PRODUCT
    parent = Path("{VX-root}/H3美术素材/待删除") if production else product / "retired-test-files"
    if batch is None:
        batch = parent / ("H3TierExport-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:12])
    else:
        batch = Path(batch)
    target = batch / relative
    files = [path] if path.is_file() else [p for p in path.rglob("*") if p.is_file()]
    rows = []
    for file in files:
        if file.is_symlink() or getattr(file.stat(), "st_file_attributes", 0) & 0x400:
            raise RuntimeError("RETIRE_REPARSE_REJECTED: " + str(file))
        rows.append({"source": str(file), "target": str(target if path.is_file() else target / file.relative_to(path)),
                     "bytes": file.stat().st_size, "sha256": hashlib.sha256(file.read_bytes()).hexdigest()})
    target.parent.mkdir(parents=True, exist_ok=True)
    os.rename(path, target)
    receipt = {"schema": "h3.tool-retirement.v1", "source": str(path), "target": str(target),
               "reason": reason, "status": "MOVED_NOT_DELETED", "files": rows}
    # A shared batch keeps one receipt per retired item so a sweep cannot clobber its own ledger.
    name = "receipt.json" if batch.name.startswith("H3TierExport-2") else "receipt-" + path.name + ".json"
    (batch / name).write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target
