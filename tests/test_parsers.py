"""
Bill-parser tests. Every case here is a real shape that was found on a real bill
and that the parser originally got WRONG -- these are regressions, not guesses.

No fixtures and no PII: each case is the offending line plus the expected
classification, written out by hand.

    python tests/test_parsers.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tampa_water", "app"))

import parsers as P  # noqa: E402

failures: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label:58} {got!r}")
    if not ok:
        failures.append(f"{label}: got {got!r}, want {want!r}")


def test_classification() -> None:
    print("classification")
    # A deposit names the service it is a deposit FOR. Classifying on the service
    # keyword booked $110 of deposits as wastewater/trash charges.
    check("DEPOSIT - WASTEWATER is a deposit",
          P._classify("DEPOSIT - WASTEWATER"), "deposit")
    check("DEPOSIT - SOLID WASTE is a deposit",
          P._classify("DEPOSIT - SOLID WASTE"), "deposit")
    # A late charge lives in the balance block; counting it over-stated the bill.
    check("LATE CHARGE FEE is not a service fee",
          P._classify("LATE CHARGE FEE 10/20/2025"), "late_fee")
    check("ACCOUNT SET-UP FEE is a fee",
          P._classify("ACCOUNT SET-UP FEE 09/08/2025"), "fee")
    check("water base", P._classify('WATER BASE CHARGE 5/8" 1 Meter'), "water_base")
    check("wastewater base", P._classify('WASTEWATER BASE CHARGE 5/8" 1 Meter'),
          "wastewater_base")
    check("wastewater volumetric", P._classify("WASTEWATER CHARGE"), "wastewater")
    check("tier", P._classify("WATER TIER 2 CHARGE"), "water_tier")
    check("tbw", P._classify("TBW PASS-THROUGH"), "tbw")
    check("solid waste", P._classify("SOLID WASTE RESIDENTIAL CHARGE"), "solid_waste")
    check("utility tax", P._classify("UTILITY TAX 10%"), "tax")


def test_line_shapes() -> None:
    print("\nline shapes")
    # No quantity sits next to the '@' on a base-charge line.
    m = P._RATED.match('WATER BASE CHARGE 5/8" 1 Meter @ 8.00 8.00')
    check("base charge parses", bool(m), True)
    if m:
        check("  base rate", P._num(m.group("rate")), 8.0)
        check("  base amount", P._num(m.group("amt")), 8.0)
    m = P._RATED.match("WATER TIER 0 CHARGE 5.0 @ 3.55 17.75")
    check("tier parses", bool(m), True)
    if m:
        check("  tier ccf", P._num(m.group("qty")), 5.0)
        check("  tier rate", P._num(m.group("rate")), 3.55)
        check("  tier cost", P._num(m.group("amt")), 17.75)
    # '%' in the label -- the tax line was silently dropped without it.
    m = P._FLAT.match("UTILITY TAX 10% 7.29")
    check("utility tax parses", bool(m), True)
    if m:
        check("  tax amount", P._num(m.group("amt")), 7.29)
    check("summary lines are not charges",
          bool(P._SKIP_FLAT.search("WATER SUBTOTAL")), True)
    check("payment lines are not charges",
          bool(P._SKIP_FLAT.search("LESS PAYMENTS")), True)


def test_full_bill() -> None:
    """A synthetic bill in the real layout, including the wrapped TBW label."""
    print("\nwhole-bill reconciliation")
    text = "\n".join([
        "07/28/2026BILL DATE:",
        "Sewer Maximum/Lawn Credit",
        "6 CCF/MO",
        "Amount Now Due $164.83",
        "Meter Number Current Previous Days of",
        "MTRXXXXXX WATER 3692 3677 31 15 11",
        "LAST BILLING 157.98",
        "LESS PAYMENTS 157.98 CR",
        'WATER BASE CHARGE 5/8" 1 Meter @ 8.00 8.00',
        "WATER TIER 0 CHARGE 5.0 @ 3.55 17.75",
        "WATER TIER 1 CHARGE 8.0 @ 4.14 33.12",
        "WATER TIER 2 CHARGE 2.0 @ 6.96 13.92",
        "TBW CHG JUL-SEP($.01)",            # label wraps onto the next line
        "OCT-DEC($.06) 2025 15.0 @ 0.01 0.15",
        "WATER SUBTOTAL 72.94",
        "UTILITY TAX 10% 7.29",
        'WASTEWATER BASE CHARGE 5/8" 1 Meter @ 8.00 8.00',
        "WASTEWATER CHARGE 6.0 @ 5.79 34.74",
        "SOLID WASTE RESIDENTIAL",
        "CHARGE 41.86",
    ])
    b = P.parse_bill_pdf_text(text)
    check("bill date", b.get("bill_date"), "2026-07-28")
    check("water CCF", b.get("water_ccf"), 15.0)
    check("water gallons", b.get("water_gallons"), 11220.0)
    check("tier count", len(b["tiers"]), 3)
    check("tier CCF sums to metered CCF",
          sum(t["ccf"] for t in b["tiers"]), b.get("water_ccf"))
    check("wrapped TBW captured", b.get("tbw_cost"), 0.15)
    check("water base", b.get("water_base_cost"), 8.0)
    check("utility tax", b.get("utility_tax_cost"), 7.29)
    check("water total", b.get("water_total_cost"), 80.23)
    check("wastewater total", b.get("wastewater_total_cost"), 42.74)
    check("solid waste", b.get("solid_waste_cost"), 41.86)
    check("sewer cap", b.get("sewer_max_ccf"), 6.0)
    # sewer is capped below water use -- irrigation is not charged sewer
    check("sewer CCF below water CCF", b.get("wastewater_ccf") < b.get("water_ccf"), True)
    check("new charges == amount due", b.get("new_charges"), b.get("amount_due"))


def test_consumption() -> None:
    print("\nconsumption table")
    html = """<table><tbody>
      <tr><td>Jul 23, 2026</td><td>Jul 28, 2026</td><td>31</td>
          <td>3692</td><td>15</td><td>0</td></tr>
      <tr><td>Jun 22, 2026</td><td>Jun 26, 2026</td><td>34</td>
          <td>3677</td><td>15</td><td>0</td></tr>
    </tbody></table>"""
    rows = P.parse_consumption(html)
    check("rows", len(rows), 2)
    check("read date", rows[0]["read_date"], "2026-07-23")
    check("days", rows[0]["days"], 31)
    check("ccf", rows[0]["ccf"], 15.0)
    check("gallons", rows[0]["gallons"], 11220.0)


def main() -> int:
    test_classification()
    test_line_shapes()
    test_full_bill()
    test_consumption()
    print()
    if failures:
        print(f"{len(failures)} FAILED")
        for f in failures:
            print("  ", f)
        return 1
    print("all parser tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
