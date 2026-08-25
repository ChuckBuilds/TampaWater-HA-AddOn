"""
Push City of Tampa water data into Home Assistant.

Three jobs:

  1. Water Dashboard  -- import billed gallons + water cost as long-term
     statistics (`tampa_water:water_consumption` / `tampa_water:water_cost`) and
     register ONE water source. Per-tier costs are published as their own
     statistics for trending, NOT as extra water sources: HA sums the volume of
     every water source, so a source per tier would multiply the gallons.

  2. Sensors -- amount due, per-tier cost, wastewater, solid waste, sewer cap.

  3. Meter validation -- for each billed service period, read the user's Flume
     and Flo totals back out of HA's recorder over THAT EXACT WINDOW and publish
     the variance. Billing periods do not align to calendar months, so comparing
     against "August" would be wrong by a week of usage.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import aiohttp

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
CORE_REST = "http://supervisor/core/api"
CORE_WS = "ws://supervisor/core/websocket"

STAT_WATER = "tampa_water:water_consumption"
STAT_WATER_COST = "tampa_water:water_cost"
STAT_WW_COST = "tampa_water:wastewater_cost"
STAT_TRASH_COST = "tampa_water:solid_waste_cost"
STAT_TIER = "tampa_water:water_tier_{n}_cost"

WATER_UNIT = "gal"

# Entities to compare the bill against. Configured by the user; empty disables it.
COMPARE_ENTITIES = [e.strip() for e in
                    (os.environ.get("COMPARE_ENTITIES") or "").split(",") if e.strip()]


def available() -> bool:
    return bool(SUPERVISOR_TOKEN)


def _headers() -> dict:
    return {"Authorization": f"Bearer {SUPERVISOR_TOKEN}",
            "Content-Type": "application/json"}


def _as_date(s) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


async def _ha_timezone(session: aiohttp.ClientSession) -> ZoneInfo:
    try:
        async with session.get(f"{CORE_REST}/config", headers=_headers()) as r:
            return ZoneInfo((await r.json()).get("time_zone", "UTC"))
    except Exception:
        return ZoneInfo("UTC")


# --------------------------------------------------------------------------- #
# statistics
# --------------------------------------------------------------------------- #
def _period_end(b: dict) -> date | None:
    """The day the meter was actually READ.

    Deliberately does NOT fall back to bill_date: Tampa bills about five days
    after the read, so using it would shift every comparison window by that gap
    and bias the result against Flume/Flo. Better to skip a bill than to compare
    the wrong week.
    """
    return _as_date(b.get("read_date"))


def _period_start(b: dict) -> date | None:
    end = _period_end(b)
    days = b.get("service_days") or b.get("days")
    if end and days:
        return end - timedelta(days=int(days))
    return end


def build_stats(bills: list[dict], tz: ZoneInfo) -> dict[str, list[dict]]:
    """One point per billing period, stamped at the period start."""
    rows = []
    for b in bills:
        # statistics only need a stable chronological stamp, so a bill with no
        # read date still charts -- it is only the METER COMPARISON that requires
        # an exact window and is skipped without one.
        start = _period_start(b) or _as_date(b.get("bill_date"))
        if start is None:
            continue
        rows.append((start, b))
    rows.sort(key=lambda r: r[0])

    out: dict[str, list[dict]] = {}
    sums: dict[str, float] = {}

    def add(stat_id: str, d: date, value) -> None:
        if value is None:
            return
        stamp = datetime(d.year, d.month, d.day, tzinfo=tz).isoformat()
        sums[stat_id] = round(sums.get(stat_id, 0.0) + float(value), 4)
        out.setdefault(stat_id, []).append(
            {"start": stamp, "state": float(value), "sum": sums[stat_id]})

    for d, b in rows:
        add(STAT_WATER, d, b.get("water_gallons"))
        add(STAT_WATER_COST, d, b.get("water_total_cost"))
        add(STAT_WW_COST, d, b.get("wastewater_total_cost"))
        add(STAT_TRASH_COST, d, b.get("solid_waste_cost"))
        for t in (b.get("tiers") or []):
            if t.get("tier") is not None:
                add(STAT_TIER.format(n=t["tier"]), d, t.get("cost"))
    return out


def _metadata(stat_id: str, name: str, unit: str) -> dict:
    return {"has_mean": False, "has_sum": True, "name": name,
            "source": "tampa_water", "statistic_id": stat_id,
            "unit_of_measurement": unit}


async def _ws(session):
    ws = await session.ws_connect(CORE_WS, heartbeat=30)
    await ws.receive_json()                                   # auth_required
    await ws.send_json({"type": "auth", "access_token": SUPERVISOR_TOKEN})
    if (await ws.receive_json()).get("type") != "auth_ok":
        await ws.close()
        return None
    return ws


async def import_statistics(session, bills: list[dict], tz, log) -> None:
    stats = build_stats(bills, tz)
    if not stats:
        log.warning("no statistics to import (no parsed bills)")
        return
    names = {
        STAT_WATER: ("Tampa Water Usage", WATER_UNIT),
        STAT_WATER_COST: ("Tampa Water Cost", "USD"),
        STAT_WW_COST: ("Tampa Wastewater Cost", "USD"),
        STAT_TRASH_COST: ("Tampa Solid Waste Cost", "USD"),
    }
    ws = await _ws(session)
    if ws is None:
        log.error("HA websocket auth failed")
        return
    try:
        mid = 1
        for stat_id, points in stats.items():
            if stat_id in names:
                name, unit = names[stat_id]
            else:                       # per-tier cost
                n = stat_id.rsplit("_", 2)[-2]
                name, unit = f"Tampa Water Tier {n} Cost", "USD"
            await ws.send_json({"id": mid, "type": "recorder/import_statistics",
                                "metadata": _metadata(stat_id, name, unit),
                                "stats": points})
            resp = await ws.receive_json()
            log.info("import_statistics %s: %s (%d points)", stat_id,
                     "ok" if resp.get("success") else resp, len(points))
            mid += 1
    finally:
        await ws.close()


# --------------------------------------------------------------------------- #
# meter validation: Flume / Flo vs the billed meter
# --------------------------------------------------------------------------- #
def window_total(series: list[dict], window_start_iso: str) -> float | None:
    """Consumption inside [window_start, end] from HA statistics buckets.

    `series` is expected to start ONE bucket before the window (see the caller).

    Preferred path is HA's per-bucket `change`, summed over in-window buckets.
    Otherwise fall back to cumulative `sum`, using the last bucket that starts
    BEFORE the window as the baseline -- subtracting the first in-window bucket
    instead would discard that bucket's own consumption.
    """
    if not series:
        return None
    ws = window_start_iso[:10]

    def in_window(p) -> bool:
        return str(p.get("start", ""))[:10] >= ws

    inside = [p for p in series if in_window(p)]
    if not inside:
        return None
    if all(p.get("change") is not None for p in inside):
        return round(sum(float(p["change"]) for p in inside), 1)

    baseline = None
    for p in series:
        if not in_window(p) and p.get("sum") is not None:
            baseline = p
    last = inside[-1]
    if last.get("sum") is None:
        return None
    if baseline is not None:
        return round(float(last["sum"]) - float(baseline["sum"]), 1)
    # no baseline available: the best we can do is first-to-last, which
    # under-counts by the first bucket. Signal that rather than mislead.
    return None


async def compare_meters(session, bills: list[dict], tz, log) -> list[dict]:
    """Sum each comparison entity over each bill's service window.

    Returns [{period_start, period_end, billed_gallons, entities:{id: {...}}}].
    """
    if not COMPARE_ENTITIES or not bills:
        return []
    ws = await _ws(session)
    if ws is None:
        return []
    results: list[dict] = []
    try:
        mid = 100
        for b in bills:
            start, end = _period_start(b), _period_end(b)
            billed = b.get("water_gallons")
            if not (start and end and billed):
                continue
            # Ask for one extra day BEFORE the window so a cumulative meter has a
            # baseline bucket to subtract from. Without it, last.sum - first.sum
            # measures from the END of the first in-window bucket and silently
            # drops that day -- a ~3% under-count over a month, which is the same
            # size as the discrepancy this check exists to find.
            s_iso = datetime(start.year, start.month, start.day,
                             tzinfo=tz).isoformat()
            base = start - timedelta(days=1)
            b_iso = datetime(base.year, base.month, base.day, tzinfo=tz).isoformat()
            e_iso = datetime(end.year, end.month, end.day, tzinfo=tz).isoformat()
            entry = {"period_start": start.isoformat(), "period_end": end.isoformat(),
                     "billed_gallons": billed, "entities": {}}
            for eid in COMPARE_ENTITIES:
                await ws.send_json({
                    "id": mid, "type": "recorder/statistics_during_period",
                    "start_time": b_iso, "end_time": e_iso,
                    "statistic_ids": [eid], "period": "day",
                    "types": ["change", "sum", "state"],
                })
                resp = await ws.receive_json()
                mid += 1
                series = (resp.get("result") or {}).get(eid) or []
                if not series:
                    continue
                # a cumulative meter: total over the window is last sum - first sum
                total = window_total(series, s_iso)
                if total is None:
                    continue
                diff = round(total - float(billed), 1)
                pct = round(diff / float(billed) * 100.0, 2) if billed else None
                entry["entities"][eid] = {"total_gallons": total,
                                          "diff_gallons": diff, "diff_pct": pct}
            if entry["entities"]:
                results.append(entry)
    finally:
        await ws.close()
    if results:
        latest = results[-1]
        for eid, v in latest["entities"].items():
            log.info("meter check %s: %s gal vs billed %s (%+.1f%%)",
                     eid, v["total_gallons"], latest["billed_gallons"],
                     v["diff_pct"] if v["diff_pct"] is not None else 0.0)
    return results


# --------------------------------------------------------------------------- #
# sensors
# --------------------------------------------------------------------------- #
def sensor_payloads(data: dict) -> list[tuple[str, object, dict]]:
    bills = data.get("bills") or []
    last = bills[0] if bills else {}
    cmp_latest = (data.get("comparison") or [{}])[-1] if data.get("comparison") else {}

    def s(eid, state, **attrs):
        attrs.setdefault("attribution", "Data provided by City of Tampa Utilities")
        return (eid, state, attrs)

    out = [
        s("sensor.tampa_water_amount_due", last.get("amount_due"),
          unit_of_measurement="USD", device_class="monetary",
          friendly_name="Tampa Water Amount Due", icon="mdi:cash"),
        s("sensor.tampa_water_last_bill_usage", last.get("water_gallons"),
          unit_of_measurement="gal", device_class="water", state_class="total",
          friendly_name="Tampa Water Last Bill Usage",
          ccf=last.get("water_ccf"), service_days=last.get("service_days")),
        s("sensor.tampa_water_last_bill_cost", last.get("water_total_cost"),
          unit_of_measurement="USD", device_class="monetary",
          friendly_name="Tampa Water Last Bill Cost", icon="mdi:water"),
        s("sensor.tampa_water_cost_per_1000_gal", last.get("cost_per_1000_gal"),
          unit_of_measurement="USD", friendly_name="Tampa Water Cost per 1000 gal",
          icon="mdi:cash-multiple"),
        s("sensor.tampa_wastewater_cost", last.get("wastewater_total_cost"),
          unit_of_measurement="USD", device_class="monetary",
          friendly_name="Tampa Wastewater Cost", icon="mdi:pipe-disconnected",
          billed_ccf=last.get("wastewater_ccf")),
        s("sensor.tampa_solid_waste_cost", last.get("solid_waste_cost"),
          unit_of_measurement="USD", device_class="monetary",
          friendly_name="Tampa Solid Waste Cost", icon="mdi:trash-can"),
        s("sensor.tampa_water_sewer_cap", last.get("sewer_max_ccf"),
          unit_of_measurement="CCF", friendly_name="Tampa Sewer Max / Lawn Credit",
          icon="mdi:water-off",
          note="wastewater is billed on this cap, not on metered water"),
        s("sensor.tampa_water_bill_date", last.get("bill_date"),
          device_class="date", friendly_name="Tampa Water Bill Date"),
        s("sensor.tampa_water_last_updated", data.get("fetched_at"),
          device_class="timestamp", friendly_name="Tampa Water Last Updated"),
    ]
    for t in (last.get("tiers") or []):
        if t.get("tier") is None:
            continue
        out.append(s(f"sensor.tampa_water_tier_{t['tier']}_cost", t.get("cost"),
                     unit_of_measurement="USD", device_class="monetary",
                     friendly_name=f"Tampa Water Tier {t['tier']} Cost",
                     icon="mdi:stairs", ccf=t.get("ccf"), rate=t.get("rate")))
    # meter validation
    for eid, v in (cmp_latest.get("entities") or {}).items():
        slug = eid.split(".")[-1]
        out.append(s(f"sensor.tampa_water_vs_{slug}", v.get("diff_pct"),
                     unit_of_measurement="%", friendly_name=f"Billed vs {slug}",
                     icon="mdi:scale-balance",
                     measured_gallons=v.get("total_gallons"),
                     billed_gallons=cmp_latest.get("billed_gallons"),
                     diff_gallons=v.get("diff_gallons"),
                     period_start=cmp_latest.get("period_start"),
                     period_end=cmp_latest.get("period_end")))
    return [(e, ("" if st is None else st), a) for e, st, a in out if st is not None]


async def update_sensors(session, data, log, quiet=False) -> None:
    n = 0
    for eid, state, attrs in sensor_payloads(data):
        try:
            async with session.post(f"{CORE_REST}/states/{eid}", headers=_headers(),
                                    json={"state": str(state), "attributes": attrs}) as r:
                if r.status in (200, 201):
                    n += 1
                else:
                    log.warning("state %s -> HTTP %s", eid, r.status)
        except Exception as e:  # noqa: BLE001
            log.warning("state %s failed: %s", eid, e)
    (log.debug if quiet else log.info)("updated %d Tampa Water entities", n)


# --------------------------------------------------------------------------- #
# water dashboard wiring
# --------------------------------------------------------------------------- #
async def configure_water(log) -> bool:
    """Register ONE water source. Never one per tier -- HA sums water sources."""
    if not available():
        return True
    try:
        async with aiohttp.ClientSession() as s:
            ws = await _ws(s)
            if ws is None:
                return False
            try:
                await ws.send_json({"id": 1, "type": "energy/get_prefs"})
                prefs = (await ws.receive_json()).get("result") or {}
                sources = prefs.get("energy_sources", [])
                mine = next((x for x in sources if x.get("type") == "water"
                             and x.get("stat_energy_from") == STAT_WATER), None)
                changed = False
                if mine is None:
                    sources.append({"type": "water",
                                    "stat_energy_from": STAT_WATER,
                                    "stat_cost": STAT_WATER_COST,
                                    "entity_energy_price": None,
                                    "number_energy_price": None})
                    changed = True
                    log.info("water dashboard: added Tampa water source (%s)", STAT_WATER)
                elif mine.get("stat_cost") != STAT_WATER_COST:
                    mine["stat_cost"] = STAT_WATER_COST
                    changed = True
                if not changed:
                    log.info("water dashboard already wired")
                    return True
                await ws.send_json({"id": 2, "type": "energy/save_prefs",
                                    "energy_sources": sources,
                                    "device_consumption":
                                        prefs.get("device_consumption", [])})
                resp = await ws.receive_json()
                if resp.get("success"):
                    log.info("water dashboard configured")
                    return True
                log.error("water save_prefs failed: %s",
                          str(resp.get("error") or resp)[:400])
                return False
            finally:
                await ws.close()
    except Exception:  # noqa: BLE001
        log.exception("configure_water failed")
        return False


async def publish(data: dict, log) -> None:
    if not available():
        return
    bills = data.get("bills") or []
    async with aiohttp.ClientSession() as session:
        tz = await _ha_timezone(session)
        try:
            await import_statistics(session, bills, tz, log)
        except Exception:  # noqa: BLE001
            log.exception("import_statistics failed")
        try:
            data["comparison"] = await compare_meters(session, bills, tz, log)
        except Exception:  # noqa: BLE001
            log.exception("meter comparison failed")
        try:
            await update_sensors(session, data, log)
        except Exception:  # noqa: BLE001
            log.exception("sensor update failed")
