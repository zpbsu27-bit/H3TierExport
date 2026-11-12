"""H3TierExport entry for local horse plus rider pose candidates."""
import sys
from creature_auto_pose import main

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "freeze":
        from mounted_pose import freeze
        raise SystemExit(freeze(sys.argv[2:]))
    raise SystemExit(main(profile="mounted"))
