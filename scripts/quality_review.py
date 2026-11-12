"""Retired legacy multi-scale review writer; the shared numeric probe remains supported."""
from __future__ import annotations

import json
import sys


def main() -> int:
    print(json.dumps({
        "schema": "h3.tier-export.companion-error.v1",
        "status": "RETIRED",
        "code": "COMPANION_RETIRED",
        "command": "quality-review",
        "replacement": "native-review",
        "error": "Legacy multi-scale writer retired. Use native-review with explicit render_scales [3]. Shared quality-tests.exe remains supported.",
    }), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
