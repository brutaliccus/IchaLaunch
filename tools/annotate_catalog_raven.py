#!/usr/bin/env python3
"""Mark bundled addons.json rows that mention Turtle WoW / TWoW.

Sets ``turtle_custom: true`` (raven icon) on matching catalog entries.
Does not contact GitHub. Existing True flags are left in place.

  python tools/annotate_catalog_raven.py
  python tools/annotate_catalog_raven.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ichalaunch.addons.catalog import (  # noqa: E402
    TURTLE_CUSTOM_FLAG,
    annotate_turtle_custom_flags,
    bundled_catalog_path,
    load_catalog_file,
    write_catalog_file,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog",
        type=Path,
        default=bundled_catalog_path(),
        help="addons.json path (default: bundled catalog)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print counts without writing",
    )
    args = parser.parse_args()
    path = Path(args.catalog)
    entries = load_catalog_file(path)
    already = sum(1 for e in entries if e.get(TURTLE_CUSTOM_FLAG) is True)
    newly = annotate_turtle_custom_flags(entries)
    marked = sum(1 for e in entries if e.get(TURTLE_CUSTOM_FLAG) is True)
    print(f"catalog={path}")
    print(f"entries={len(entries)} already_marked={already} newly_marked={newly} marked={marked}")
    if args.dry_run:
        return 0
    write_catalog_file(path, entries)
    print("wrote catalog")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
