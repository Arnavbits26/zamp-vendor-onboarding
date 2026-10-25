"""
Look at past runs.

    python -m app.history              # every run, newest first
    python -m app.history SUB-1005     # all versions of one submission, with what changed
"""
import sys

from app.run_pipeline import show_changes
from app.changes import compare_versions
from app.store import DEFAULT_DB, Store


def main():
    store = Store(DEFAULT_DB)
    if len(sys.argv) > 1:
        sid = sys.argv[1]
        versions = store.versions(sid)
        if not versions:
            sys.exit(f"No runs found for {sid}")
        for i, v in enumerate(versions):
            print(f"\nVersion {v['version']}: {v['status']}  -  {v['headline']}")
            if i:
                show_changes(compare_versions(versions[i - 1], v))
        return
    rows = store.list_runs()
    if not rows:
        sys.exit("No runs recorded yet. Try:  python -m app.run_pipeline --demo-resubmission")
    print(f"{'run':>4}  {'submission':<10} {'v':>2}  {'status':<9} {'vendor':<38} when (UTC)")
    for r in rows:
        print(f"{r['run_id']:>4}  {r['submission_id']:<10} {r['version']:>2}  {r['status']:<9} "
              f"{r['vendor_name'][:36]:<38} {r['created_at']}")


if __name__ == "__main__":
    main()
