#!/usr/bin/env python3
"""Tiny module the demo config scores. Real behaviour, no dependencies.

    python3 examples/demo/demo_app.py --check      # prints OK summary={...}
    python3 examples/demo/demo_app.py --selftest   # asserts, exits non-zero on break
"""
from __future__ import annotations

SAMPLE = [
    {"team": "a", "value": 2},
    {"team": "a", "value": 3},
    {"team": "b", "value": 4},
    {"team": "c", "value": None},
]


def summarize(rows: list[dict]) -> dict:
    """Sum 'value' per 'team'; rows without a number are ignored, not zeroed."""
    out: dict = {}
    for row in rows:
        value = row.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        team = row.get("team", "?")
        out[team] = out.get(team, 0) + value
    return out


def selftest() -> int:
    s = summarize(SAMPLE)
    assert s["a"] == 5, s
    assert s["b"] == 4, s
    assert "c" not in s, "a row with no number must not create a team"
    assert summarize([]) == {}
    return 0


if __name__ == "__main__":
    import sys

    if "--selftest" in sys.argv:
        selftest()
        print("OK selftest=4 assertions")
        raise SystemExit(0)
    print(f"OK summary={summarize(SAMPLE)}")
