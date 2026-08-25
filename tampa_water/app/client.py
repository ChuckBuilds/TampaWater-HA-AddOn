"""
City of Tampa Customer Self Service client.

No browser: the portal is a Java/Spring app with a plain form login and a _csrf
token, and it serves no captcha. requests + BeautifulSoup is enough.

The portal splits the data across two sources that MUST be joined:

  consumption chart -> read_date, bill_date, days, meter_read, CCF
  bill PDF          -> the itemised charges (tiers, wastewater, solid waste)

The PDF carries the BILL date but not the READ date, and they differ by about
five days. Every meter comparison is summed over a bill's service window, so
using the bill date would shift each window by that gap and bias the result
against Flume/Flo. The join below puts the real read date on every bill.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

try:
    from . import parsers
except ImportError:
    import parsers  # type: ignore

LOG = logging.getLogger("tampa_water.client")

BASE = "https://utilities.tampagov.net"
LOGIN_FORM = BASE + "/css/public/login/form"
LOGIN_POST = BASE + "/css/public/login"
TRANSACTIONS = BASE + "/css/account/accountTransaction"
CONSUMPTION = BASE + "/css/utility/consumptionChart/{loc}/{seq}"
BILL_PDF = BASE + "/css//billPrint/retrieve/{doc}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def mask(v) -> str:
    """Redact identifier-shaped values before they reach a log line."""
    return re.sub(r"(?<!\d)\d{5,}(?!\d)",
                  lambda m: "..." + m.group(0)[-4:], str(v))


class TampaWaterClient:
    def __init__(self, user: str, password: str) -> None:
        self._user, self._pass = user, password
        self._s = requests.Session()
        self._s.headers["User-Agent"] = UA
        self._loc: str | None = None

    # ---- auth ------------------------------------------------------------- #
    def login(self) -> None:
        r = self._s.get(LOGIN_FORM, timeout=30)
        r.raise_for_status()
        tok = BeautifulSoup(r.text, "html.parser").find("input", {"name": "_csrf"})
        if not tok or not tok.get("value"):
            raise RuntimeError("no _csrf token on the login form; flow changed")
        r = self._s.post(LOGIN_POST,
                         data={"_csrf": tok["value"], "username": self._user,
                               "password": self._pass, "submit": "Sign In"},
                         headers={"Referer": LOGIN_FORM}, timeout=30)
        r.raise_for_status()
        if "/public/login" in r.url:
            raise RuntimeError("login failed (check credentials)")
        m = re.search(r"consumptionChart/(\d+)/(\d+)", r.text)
        if m:
            self._loc = m.group(1)
        LOG.info("login OK (location %s)", mask(self._loc))
        self._landing = r.text

    # ---- raw fetches ------------------------------------------------------ #
    def consumption(self, seq: int = 2) -> list[dict]:
        if not self._loc:
            return []
        r = self._s.get(CONSUMPTION.format(loc=self._loc, seq=seq), timeout=30)
        return parsers.parse_consumption(r.text)

    def bill_doc_ids(self) -> list[str]:
        r = self._s.get(TRANSACTIONS, timeout=30)
        return sorted(set(re.findall(r"billPrint/retrieve/(\d+)", r.text)))

    def bill(self, doc_id: str) -> dict:
        r = self._s.get(BILL_PDF.format(doc=doc_id), timeout=60)
        r.raise_for_status()
        b = parsers.parse_bill_pdf_text(parsers.extract_pdf_text(r.content))
        b["doc_id"] = doc_id
        return b

    # ---- the join --------------------------------------------------------- #
    @staticmethod
    def merge(bills: list[dict], usage: list[dict]) -> list[dict]:
        """Attach each consumption row's READ date to its bill.

        Matched on bill_date, falling back to the nearest read within 10 days --
        an unmatched bill keeps read_date None rather than silently borrowing the
        bill date, so a comparison is skipped instead of being computed wrong.
        """
        by_bill_date = {u["bill_date"]: u for u in usage if u.get("bill_date")}
        for b in bills:
            bd = b.get("bill_date")
            u = by_bill_date.get(bd)
            if u is None and bd:
                try:
                    target = date.fromisoformat(bd)
                except ValueError:
                    target = None
                if target:
                    best, best_gap = None, timedelta(days=11)
                    for cand in usage:
                        rd = cand.get("read_date")
                        if not rd:
                            continue
                        gap = abs(date.fromisoformat(rd) - target)
                        if gap < best_gap:
                            best, best_gap = cand, gap
                    u = best
            if u:
                b["read_date"] = u.get("read_date")
                b["meter_read"] = b.get("current_read") or u.get("meter_read")
                b.setdefault("service_days", u.get("days"))
                b["usage_ccf_portal"] = u.get("ccf")
        return bills

    # ---- everything ------------------------------------------------------- #
    def fetch_all(self, max_bills: int = 24) -> dict:
        self.login()
        usage = self.consumption()
        LOG.info("consumption rows: %d", len(usage))
        ids = self.bill_doc_ids()[-max_bills:]
        bills = []
        for doc in ids:
            try:
                bills.append(self.bill(doc))
            except Exception:  # noqa: BLE001
                LOG.exception("failed to parse bill %s", mask(doc))
        LOG.info("bills parsed: %d of %d", len(bills), len(ids))
        bills = self.merge(bills, usage)

        unmatched = [b for b in bills if not b.get("read_date")]
        if unmatched:
            LOG.warning("%d bill(s) have no meter read date; their service window "
                        "is unknown so meter comparison will skip them", len(unmatched))

        bills.sort(key=lambda b: b.get("bill_date") or "", reverse=True)
        return {"bills": bills, "usage": usage}
