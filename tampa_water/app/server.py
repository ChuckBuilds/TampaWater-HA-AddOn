"""
Tampa Water add-on server.

Polls the City of Tampa CSS portal, keeps a never-purged bill archive on disk,
pushes statistics + sensors into Home Assistant, and serves a dashboard over
ingress.

Security posture is deliberate (these are the lessons from the sibling TECO
add-on, applied from the start rather than retrofitted):
  - ingress is proven by the request SOURCE ADDRESS, never by a header
  - the token is compared in constant time
  - the API port is not published by default
  - interactive API docs are disabled

ENV:
  TAMPA_USER, TAMPA_PASS   (required)
  SIDECAR_TOKEN            (optional) require 'X-Auth-Token' on data endpoints
  POLL_INTERVAL_HOURS      (default 12)  the bill only changes monthly
  BACKFILL_BILLS           (default 24)
  CACHE_DIR                (default ./cache)
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import secrets
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import client as tampa_client  # noqa: E402
import ha_publish  # noqa: E402

LOG = logging.getLogger("tampa_water")
# `log_level` is an add-on option; honour it rather than pinning INFO
_LEVEL = (os.environ.get("LOG_LEVEL") or "info").upper()
logging.basicConfig(level=getattr(logging, _LEVEL, logging.INFO),
                    format="%(asctime)s %(levelname)s %(message)s")

# pypdf is noisy about malformed-but-readable structures in these bills
logging.getLogger("pypdf").setLevel(logging.ERROR)

POLL_INTERVAL = max(1, int(os.environ.get("POLL_INTERVAL_HOURS", "12"))) * 3600
SENSOR_REFRESH = max(1, int(os.environ.get("SENSOR_REFRESH_MIN", "10"))) * 60
BACKFILL_BILLS = int(os.environ.get("BACKFILL_BILLS", "24"))
SETUP_WATER = os.environ.get("SETUP_WATER_DASHBOARD", "1") != "0"
TOKEN = os.environ.get("SIDECAR_TOKEN") or ""
CACHE_DIR = os.environ.get("CACHE_DIR", os.path.join(os.path.dirname(__file__), "cache"))
CACHE_FILE = os.path.join(CACHE_DIR, "bills.json")

# Home Assistant proxies ingress from the Supervisor's internal network. Only a
# request that genuinely arrives from there may skip the token -- the header
# alone proves nothing, since any client can send any header.
SUPERVISOR_NET = ipaddress.ip_network("172.30.32.0/23")


def _load_cache() -> dict:
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    os.replace(tmp, CACHE_FILE)


class TampaSession:
    """Owns the archive. Bills are keyed by doc id and never purged, so history
    outlives the ~11 months the portal keeps."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._cache: dict = _load_cache()
        self._last_data: dict | None = None

    async def fetch_all(self, force: bool = False) -> dict:
        async with self._lock:
            user = os.environ.get("TAMPA_USER")
            pw = os.environ.get("TAMPA_PASS")
            if not user or not pw:
                raise RuntimeError("TAMPA_USER / TAMPA_PASS not set")

            def work():
                c = tampa_client.TampaWaterClient(user, pw)
                return c.fetch_all(max_bills=BACKFILL_BILLS)

            got = await asyncio.get_running_loop().run_in_executor(None, work)

            new = 0
            for b in got["bills"]:
                key = b.get("doc_id") or b.get("bill_date")
                if not key:
                    continue
                if key not in self._cache or force:
                    new += 1
                self._cache[key] = b
            if new:
                _save_cache(self._cache)
                LOG.info("archive: +%d bill(s), %d total", new, len(self._cache))

            bills = sorted(self._cache.values(),
                           key=lambda b: b.get("bill_date") or "", reverse=True)
            result = {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "bills": bills,
                "usage": got.get("usage", []),
                "counts": {"bills": len(bills),
                           "usage_rows": len(got.get("usage", [])),
                           "archived": len(self._cache)},
            }
            self._last_data = result
            return result

    def export(self) -> dict:
        bills = sorted(self._cache.values(),
                       key=lambda b: b.get("bill_date") or "", reverse=True)
        return {"exported_at": datetime.now(timezone.utc).isoformat(),
                "archived": len(self._cache), "bills": bills}


session = TampaSession()


async def _poll_loop() -> None:
    last = 0.0
    water_configured = False
    while True:
        try:
            now = time.time()
            if session._last_data is None or (now - last) >= POLL_INTERVAL:
                data = await session.fetch_all()
                last = now
                if ha_publish.available():
                    await ha_publish.publish(data, LOG)
                    if SETUP_WATER and not water_configured:
                        water_configured = await ha_publish.configure_water(LOG)
                else:
                    LOG.info("refreshed (%d bills); HA publish skipped "
                             "(no SUPERVISOR_TOKEN)", data["counts"]["archived"])
            elif ha_publish.available() and session._last_data:
                import aiohttp
                async with aiohttp.ClientSession() as s:
                    await ha_publish.update_sensors(s, session._last_data, LOG, quiet=True)
        except Exception:  # noqa: BLE001
            LOG.exception("poll cycle failed")
        await asyncio.sleep(SENSOR_REFRESH)


try:
    from contextlib import asynccontextmanager

    from fastapi import Depends, FastAPI, Header, HTTPException, Request
    from fastapi.responses import HTMLResponse, JSONResponse, Response

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(_poll_loop())
        try:
            yield
        finally:
            task.cancel()

    app = FastAPI(title="Tampa Water", version="1.0.0", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    if not TOKEN:
        LOG.warning("no auth_token set: if you publish the API port in the add-on's "
                    "Network tab, ANY device on your LAN can read your billing "
                    "archive. Ingress (the sidebar panel) is unaffected.")

    _UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui.html")

    def _from_supervisor(request: Request) -> bool:
        host = getattr(getattr(request, "client", None), "host", None)
        if not host:
            return False
        try:
            return ipaddress.ip_address(host) in SUPERVISOR_NET
        except ValueError:
            return False

    def _auth(request: Request, x_auth_token: str | None = Header(default=None)) -> None:
        """Require the token when one is set. Ingress is proven by the source
        address; the X-Ingress-Path header alone is forgeable and is not trusted."""
        if not TOKEN:
            return
        if request.headers.get("X-Ingress-Path") is not None and _from_supervisor(request):
            return
        if not x_auth_token or not secrets.compare_digest(x_auth_token, TOKEN):
            raise HTTPException(status_code=401, detail="bad or missing X-Auth-Token")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        try:
            with open(_UI, encoding="utf-8") as f:
                return HTMLResponse(f.read())
        except FileNotFoundError:
            return HTMLResponse("<h1>Tampa Water</h1><p>webui.html not found.</p>")

    @app.get("/health")
    async def health():
        return {"ok": True, "archived_bills": len(session._cache)}

    @app.get("/data")
    async def data(force: bool = False, _=Depends(_auth)):
        try:
            return JSONResponse(await session.fetch_all(force=force))
        except Exception as e:
            LOG.exception("fetch failed")
            raise HTTPException(status_code=502, detail=str(e))

    @app.get("/export")
    async def export(format: str = "json", _=Depends(_auth)):
        d = session.export()
        if format.lower() == "csv":
            import csv
            import io
            buf = io.StringIO()
            w = csv.writer(buf)
            cols = ("bill_date", "read_date", "service_days", "water_ccf",
                    "water_gallons", "water_base_cost", "water_tier_cost",
                    "tbw_cost", "utility_tax_cost", "water_total_cost",
                    "wastewater_ccf", "wastewater_total_cost", "solid_waste_cost",
                    "new_charges", "amount_due", "sewer_max_ccf")
            w.writerow(cols)
            for b in d["bills"]:
                w.writerow([b.get(c) for c in cols])
            return Response(content=buf.getvalue(), media_type="text/csv",
                            headers={"Content-Disposition":
                                     "attachment; filename=tampa_water.csv"})
        return JSONResponse(d)

except ImportError:
    app = None


async def _run_once() -> int:
    data = await session.fetch_all()
    c = data["counts"]
    print(f"\nOK - bills={c['bills']} usage_rows={c['usage_rows']} "
          f"archived={c['archived']}")
    b = data["bills"][0] if data["bills"] else {}
    print("newest bill:", json.dumps({k: b.get(k) for k in
          ("bill_date", "read_date", "service_days", "water_ccf", "water_gallons",
           "water_total_cost", "wastewater_total_cost", "solid_waste_cost",
           "amount_due")}, indent=1))
    os.makedirs(CACHE_DIR, exist_ok=True)
    out = os.path.join(CACHE_DIR, "last_payload.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    print("full payload ->", out)
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    if args.once:
        raise SystemExit(asyncio.run(_run_once()))
    print("run: uvicorn server:app --host 0.0.0.0 --port 8099")
