#!/usr/bin/env python3
"""Create deterministic, disjoint JSONL shards for shared-disk workers."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("inventory", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--shards", type=int, default=2)
    args = parser.parse_args()
    if args.shards < 1:
        parser.error("--shards must be positive")
    rows = json.loads(args.inventory.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for shard in range(args.shards):
        selected = [row for index, row in enumerate(rows) if index % args.shards == shard]
        target = args.output_dir / f"fateh-{shard:02d}-of-{args.shards:02d}.jsonl"
        target.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in selected),
                          encoding="utf-8")
        print(f"{target}: {len(selected)}")


if __name__ == "__main__":
    main()
