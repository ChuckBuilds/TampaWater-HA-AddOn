"""
Window arithmetic for the meter comparison.

Regression: the first version computed last.sum - first.sum over buckets INSIDE
the window, which measures from the END of the first bucket and silently drops
that bucket's consumption -- about 3% over a month, the same magnitude as the
device discrepancy this feature exists to detect.

    python tests/test_window.py
"""
from __future__ import annotations

import os
import sys
import types

sys.modules.setdefault("aiohttp", types.ModuleType("aiohttp"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tampa_water", "app"))

import ha_publish as H  # noqa: E402

failures: list[str] = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label:56} {got!r}")
    if not ok:
        failures.append(f"{label}: got {got!r} want {want!r}")


def main() -> int:
    # A meter using 100 gal/day for 4 days. The window is days 2-4 (300 gal).
    # The series includes day 1 as the baseline bucket, as the caller requests.
    cumulative = [
        {"start": "2026-07-01T00:00:00+00:00", "sum": 100.0, "change": 100.0},
        {"start": "2026-07-02T00:00:00+00:00", "sum": 200.0, "change": 100.0},
        {"start": "2026-07-03T00:00:00+00:00", "sum": 300.0, "change": 100.0},
        {"start": "2026-07-04T00:00:00+00:00", "sum": 400.0, "change": 100.0},
    ]
    win = "2026-07-02T00:00:00+00:00"

    print("with per-bucket change available")
    check("uses change, counts every in-window day",
          H.window_total(cumulative, win), 300.0)

    print("\nfalling back to cumulative sum")
    no_change = [{k: v for k, v in p.items() if k != "change"} for p in cumulative]
    check("subtracts the PRE-window baseline, not the first in-window bucket",
          H.window_total(no_change, win), 300.0)
    # the old, wrong arithmetic would have produced 200.0 here
    check("is not the old off-by-one result",
          H.window_total(no_change, win) != 200.0, True)

    print("\nedge cases")
    check("empty series", H.window_total([], win), None)
    check("no in-window buckets",
          H.window_total(no_change[:1], "2026-07-03T00:00:00+00:00"), None)
    # without a baseline bucket we cannot compute honestly -- must not guess
    check("no baseline and no change -> None rather than an under-count",
          H.window_total(no_change[1:], win), None)
    check("single in-window bucket with change",
          H.window_total(cumulative[:2], win), 100.0)

    print()
    if failures:
        print(f"{len(failures)} FAILED")
        for f in failures:
            print("  ", f)
        return 1
    print("all window tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
