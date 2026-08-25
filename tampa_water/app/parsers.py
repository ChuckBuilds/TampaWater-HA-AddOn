"""
Parsers for the City of Tampa Customer Self Service portal (Cayenta CSS).

Two very different sources, because the portal splits the data:

  consumption chart HTML  -> per-period meter reads and CCF used (~11 months)
  bill PDF                -> the itemised charges: water tiers, base charges,
                             wastewater, solid waste, tax. Nothing else exposes
                             the tier breakdown -- CSS_BILL_HISTORY has totals only.

Tier rates change by ordinance every October, so rates are READ OFF THE BILL
rather than hardcoded.
"""
from __future__ import annotations

import re
from datetime import date, datetime

CCF_TO_GALLONS = 748.0


def _num(text) -> float | None:
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).replace(",", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    v = float(m.group(0))
    return -v if s.upper().endswith("CR") else v


def _date(text) -> date | None:
    if not text:
        return None
    t = str(text).strip()
    for fmt in ("%b %d, %Y", "%m/%d/%Y", "%B %d, %Y", "%m/%d/%y"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
# Consumption chart: Read Date | Bill Date | Days | Meter Read | Consumption |
#                    Average Daily
# --------------------------------------------------------------------------- #
def parse_consumption(html: str) -> list[dict]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []
    out: list[dict] = []
    for tr in (table.select("tbody tr") or table.find_all("tr")[1:]):
        c = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(c) < 5:
            continue
        ccf = _num(c[4])
        read_date, bill_date, days = _date(c[0]), _date(c[1]), _num(c[2])
        if read_date is None and bill_date is None:
            continue
        out.append({
            "read_date": read_date.isoformat() if read_date else None,
            "bill_date": bill_date.isoformat() if bill_date else None,
            "days": int(days) if days is not None else None,
            "meter_read": _num(c[3]),
            "ccf": ccf,
            "gallons": round(ccf * CCF_TO_GALLONS, 1) if ccf is not None else None,
        })
    return out


# --------------------------------------------------------------------------- #
# Bill PDF: the itemised charges
# --------------------------------------------------------------------------- #
# Charges are parsed STRUCTURALLY, not by matching known labels. Tampa varies the
# wording between bills -- the Tampa Bay Water pass-through appears as both
#   "TBW PASS-THROUGH 15.0 @ 0.01 0.15"
# and, wrapped across two lines,
#   "TBW CHG JUL-SEP($.01)" / "OCT-DEC($.06) 2025 7.0 @ 0.07 0.49"
# Matching the shape "<label> <qty> @ <rate> <amount>" survives that. Matching
# labels did not, and silently dropped $0.49 off a bill.
_RATED = re.compile(
    r"^(?P<label>.*?)(?:(?P<qty>[\d,]+\.?\d*)\s*)?@\s*(?P<rate>[\d,]+\.?\d*)"
    r"\s+(?P<amt>-?[\d,]+\.\d{2})\s*$")
_FLAT = re.compile(
    r"^(?P<label>[A-Z][A-Z0-9 %./()'\"&-]{3,}?)\s+(?P<amt>-?[\d,]+\.\d{2})"
    r"(?:\s*(?P<cr>CR))?\s*$")
_TIER_N = re.compile(r"TIER\s+(\d+)", re.I)
_SEWER_CAP = re.compile(r"([\d.]+)\s*CCF/MO", re.I)
_BILL_DATE = re.compile(
    r"(\d{2}/\d{2}/\d{4})\s*BILL\s*DATE|BILL\s*DATE:?\s*(\d{2}/\d{2}/\d{4})", re.I)
_DUE = re.compile(r"Amount\s+Now\s+Due\s*\$?([\d.,]+)", re.I)
_METER = re.compile(
    r"(\S+)\s+WATER\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)(?:\s+(\d+))?", re.I)
# running-balance and summary lines are not charges
_SKIP_FLAT = re.compile(
    r"LAST BILLING|LESS PAYMENTS|SUBTOTAL|AMOUNT|BALANCE|TOTAL|PAY |DUE", re.I)


def _classify(label: str) -> str:
    L = label.upper()
    # one-time charges first: "DEPOSIT - WASTEWATER" and "DEPOSIT - SOLID WASTE"
    # contain service names and would otherwise be booked as service charges,
    # inflating wastewater/trash and hiding the deposit.
    if "DEPOSIT" in L:
        return "deposit"
    # A late charge sits in the balance-activity block, not current charges --
    # "Amount Now Due" excludes it, so counting it over-states the bill. Kept as
    # its own kind so the money is still visible.
    if "LATE" in L:
        return "late_fee"
    if "SET-UP" in L or "SET UP" in L or "FEE" in L:
        return "fee"
    if "TIER" in L:
        return "water_tier"
    if "TBW" in L or "PASS-THROUGH" in L or "PASS THROUGH" in L:
        return "tbw"
    if "WASTEWATER" in L or "SEWER" in L:
        return "wastewater_base" if "BASE" in L else "wastewater"
    if "WATER" in L and "BASE" in L:
        return "water_base"
    if "SOLID WASTE" in L or "TRASH" in L or "RECYCL" in L:
        return "solid_waste"
    if "TAX" in L:
        return "tax"
    return "other"


def parse_bill_pdf_text(text: str) -> dict:
    """Itemise a bill. Returns line items plus reconciled totals."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    items: list[dict] = []

    for i, line in enumerate(lines):
        m = _RATED.match(line)
        if m:
            label = m.group("label").strip()
            kind = _classify(label)
            # The label may have wrapped. Tampa's TBW line splits as
            #   "TBW CHG JUL-SEP($.01)" / "OCT-DEC($.06) 2025 7.0 @ 0.07 0.49"
            # so the half carrying the numbers has no recognisable label at all.
            # Borrow the previous line when it is an orphan (not itself a charge)
            # and this label alone tells us nothing.
            if (kind == "other" or len(label) < 4) and i > 0:
                prev = lines[i - 1]
                if not _RATED.match(prev) and not _FLAT.match(prev):
                    joined = (prev + " " + label).strip()
                    if _classify(joined) != "other":
                        label, kind = joined, _classify(joined)
            it = {"kind": kind, "label": label, "qty": _num(m.group("qty")),
                  "rate": _num(m.group("rate")), "cost": _num(m.group("amt"))}
            if kind == "water_tier" and (tm := _TIER_N.search(label)):
                it["tier"] = int(tm.group(1))
            items.append(it)
            continue
        m = _FLAT.match(line)
        if m and not _SKIP_FLAT.search(m.group("label")):
            label = m.group("label").strip()
            # "SOLID WASTE RESIDENTIAL" / "CHARGE 41.86" wraps; join backwards
            if label.upper().startswith("CHARGE") and i > 0:
                label = (lines[i - 1] + " " + label).strip()
            amt = _num(m.group("amt"))
            if m.group("cr"):
                amt = -abs(amt or 0)
            items.append({"kind": _classify(label), "label": label,
                          "qty": None, "rate": None, "cost": amt})

    out: dict = {"source": "pdf", "items": items}

    def total(kind: str):
        v = sum(i["cost"] or 0 for i in items if i["kind"] == kind)
        return round(v, 2) if v else None

    out["tiers"] = sorted(
        [{"tier": i.get("tier"), "ccf": i["qty"], "rate": i["rate"], "cost": i["cost"]}
         for i in items if i["kind"] == "water_tier"],
        key=lambda t: (t["tier"] is None, t["tier"]))
    out["water_tier_cost"] = total("water_tier")
    out["water_base_cost"] = total("water_base")
    out["tbw_cost"] = total("tbw")
    out["utility_tax_cost"] = total("tax")
    out["wastewater_base_cost"] = total("wastewater_base")
    out["wastewater_cost"] = total("wastewater")
    out["solid_waste_cost"] = total("solid_waste")
    out["deposit_cost"] = total("deposit")
    out["late_fee_cost"] = total("late_fee")
    out["fee_cost"] = total("fee")
    out["other_cost"] = total("other")

    for it in items:
        if it["kind"] == "wastewater" and it["qty"]:
            out["wastewater_ccf"] = it["qty"]

    if (m := _SEWER_CAP.search(text)):
        out["sewer_max_ccf"] = _num(m.group(1))
    if (m := _BILL_DATE.search(text)):
        d = _date(m.group(1) or m.group(2))
        out["bill_date"] = d.isoformat() if d else None
    if (m := _DUE.search(text)):
        out["amount_due"] = _num(m.group(1))
    if (m := _METER.search(text)):
        out["meter_number"] = m.group(1)
        out["current_read"] = _num(m.group(2))
        out["previous_read"] = _num(m.group(3))
        out["service_days"] = int(_num(m.group(4)) or 0) or None
        out["water_ccf"] = _num(m.group(5))

    ccf = out.get("water_ccf")
    if ccf is not None:
        out["water_gallons"] = round(ccf * CCF_TO_GALLONS, 1)

    # Water-dashboard cost = the volumetric water side only. Wastewater and solid
    # waste are real money but not water volume, so they are tracked separately.
    water_total = sum(v for v in (out["water_base_cost"], out["water_tier_cost"],
                                  out["tbw_cost"], out["utility_tax_cost"]) if v)
    out["water_total_cost"] = round(water_total, 2) if water_total else None
    ww = sum(v for v in (out["wastewater_base_cost"], out["wastewater_cost"]) if v)
    out["wastewater_total_cost"] = round(ww, 2) if ww else None

    # New charges on THIS bill, including one-time deposits/fees. Distinct from
    # amount_due, which also carries any unpaid prior balance.
    new_charges = sum(v for v in (out["water_total_cost"], out["wastewater_total_cost"],
                                  out["solid_waste_cost"], out["deposit_cost"],
                                  out["fee_cost"], out["other_cost"]) if v)
    out["new_charges"] = round(new_charges, 2) if new_charges else None
    if ccf and out.get("water_total_cost"):
        out["cost_per_1000_gal"] = round(
            out["water_total_cost"] / (ccf * CCF_TO_GALLONS / 1000.0), 4)
    return out


def extract_pdf_text(path_or_bytes) -> str:
    from io import BytesIO

    from pypdf import PdfReader
    src = BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else path_or_bytes
    return "\n".join((p.extract_text() or "") for p in PdfReader(src).pages)
