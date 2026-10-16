#!/usr/bin/env python3
"""Deliberately broken twin of demo_app.py — used by loop.demo-fail.json.

It keeps the classic false green: `--selftest` passes because it asserts nothing, while the
behaviour is wrong (the per-team breakdown is collapsed into one total). The loop catches it in the
math check, not in the self-test. Run it to see the critic fail an artifact that "passed its tests".

    python3 scripts/loop_runner.py --config examples/demo/loop.demo-fail.json
"""
from __future__ import annotations

SAMPLE = [
    {"team": "a", "value": 2},
    {"team": "a", "value": 3},
    {"team": "b", "value": 4},
    {"team": "c", "value": None},
]


def summarize(rows: list[dict]) -> dict:
    return {"total": sum(r.get("value") or 0 for r in rows)}


def selftest() -> int:
    summarize(SAMPLE)  # asserts nothing — exactly the bug this tool exists to find
    return 0


if __name__ == "__main__":
    import sys

    if "--selftest" in sys.argv:
        selftest()
        print("OK selftest")
        raise SystemExit(0)
    print(f"OK summary={summarize(SAMPLE)}")
