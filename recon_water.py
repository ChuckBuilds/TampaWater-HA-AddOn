"""
Map the City of Tampa Customer Self Service portal (Cayenta CSS).

Logs in the way the add-on will, then dumps every reachable account page to
recon_out/ and reports what data is actually available: usage history depth,
tier itemisation, and which bill components are broken out.

Everything printed to the console is MASKED. The raw HTML in recon_out/ is not
masked (we need it to build parsers) -- recon_out/ is gitignored.

    ./.venv/Scripts/python.exe recon_water.py
"""
from __future__ import annotations

import os
import re
import sys

import requests
from bs4 import BeautifulSoup

BASE = "https://utilities.tampagov.net"
LOGIN_FORM = BASE + "/css/public/login/form"
LOGIN_POST = BASE + "/css/public/login"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recon_out")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def load_env(path="water.env") -> dict:
    """Read the creds file directly -- NOT via the shell, so a '$' in the
    password is not expanded away."""
    vals = {}
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                vals[k.strip()] = v
    return vals


def mask(s) -> str:
    """Redact anything identifier-shaped before it reaches the console."""
    if s is None:
        return "None"
    s = str(s)
    s = re.sub(r"(?<!\d)\d{5,}(?!\d)", lambda m: f"<{len(m.group(0))}-digits>", s)
    s = re.sub(r"\b\d{3,6}\s+[NSEW]\.?\s+\w+\s+(ST|AVE|BLVD|DR|RD|LN|CT|PL|WAY)\b",
               "<address>", s, flags=re.I)
    s = re.sub(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b", "<email>", s)
    return s


def save(name: str, text: str) -> None:
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
        f.write(text)


def main() -> int:
    env = load_env()
    user, pw = env.get("TAMPA_USER"), env.get("TAMPA_PASS")
    if not user or not pw:
        print("TAMPA_USER / TAMPA_PASS missing from water.env")
        return 1

    s = requests.Session()
    s.headers["User-Agent"] = UA

    # 1. login form -> CSRF token
    r = s.get(LOGIN_FORM, timeout=30)
    print(f"login form: HTTP {r.status_code}")
    soup = BeautifulSoup(r.text, "html.parser")
    tok = soup.find("input", {"name": "_csrf"})
    if not tok or not tok.get("value"):
        print("  no _csrf token found -- login flow has changed")
        save("login_form.html", r.text)
        return 1
    print(f"  _csrf present ({len(tok['value'])} chars)")

    # 2. authenticate
    r = s.post(LOGIN_POST,
               data={"_csrf": tok["value"], "username": user, "password": pw,
                     "submit": "Sign In"},
               headers={"Referer": LOGIN_FORM}, timeout=30, allow_redirects=True)
    print(f"login post: HTTP {r.status_code} -> {r.url.replace(BASE,'')}")
    body = r.text
    lowered = body.lower()
    failed = ("/login" in r.url and "form" in r.url) or "invalid" in lowered[:6000]
    if failed:
        print("  LOGIN FAILED (bad credentials, or the portal wants something extra)")
        save("login_failed.html", body)
        for kw in ("captcha", "locked", "invalid", "incorrect", "verify", "security question"):
            if kw in lowered:
                print(f"    page mentions: {kw!r}")
        return 1
    print("  login OK")
    save("landing.html", body)

    # 3. crawl the authenticated nav
    soup = BeautifulSoup(body, "html.parser")
    links = {}
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if h.startswith("/css/") and "logout" not in h.lower():
            links[h] = a.get_text(" ", strip=True)[:40]
    print(f"\nnav links found: {len(links)}")
    for h, t in sorted(links.items()):
        print(f"  {t:40} {h}")

    candidates = list(links) + [
        "/css/account/getAccount", "/css/account/consumption",
        "/css/account/billing", "/css/account/history",
        "/css/account/transactions", "/css/account/usage",
    ]
    seen = set()
    print("\nfetching pages:")
    pages = {}
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        try:
            rr = s.get(BASE + path, timeout=30)
        except Exception as e:  # noqa: BLE001
            print(f"  {path:44} ERROR {e}")
            continue
        fn = path.strip("/").replace("/", "_") + ".html"
        save(fn, rr.text)
        sp = BeautifulSoup(rr.text, "html.parser")
        tables = sp.find_all("table")
        rows = sum(len(t.select("tbody tr")) or len(t.find_all("tr")) for t in tables)
        pages[path] = sp
        print(f"  {path:44} {rr.status_code}  {len(rr.text):>7}b  "
              f"tables={len(tables)} rows={rows}")

    # 4. what's actually in those tables?
    print("\n--- table headers (masked) ---")
    for path, sp in pages.items():
        for i, t in enumerate(sp.find_all("table")):
            heads = [th.get_text(" ", strip=True)[:20] for th in t.find_all("th")]
            if not heads:
                continue
            body_rows = t.select("tbody tr") or t.find_all("tr")[1:]
            print(f"  {path} [table {i}] rows={len(body_rows)}")
            print(f"      {heads}")
            for tr in body_rows[:2]:
                cells = [mask(td.get_text(' ', strip=True))[:20]
                         for td in tr.find_all("td")]
                if cells:
                    print(f"      e.g. {cells}")

    # 5. keyword sweep: which bill components / tiers are named anywhere?
    print("\n--- component keywords across all pages ---")
    allhtml = " ".join(sp.get_text(" ", strip=True).lower() for sp in pages.values())
    for kw in ("tier", "ccf", "gallon", "consumption", "usage", "water",
               "wastewater", "sewer", "solid waste", "trash", "recycl",
               "stormwater", "reclaimed", "tampa bay water", "utility tax",
               "base charge", "service charge"):
        n = allhtml.count(kw)
        print(f"  {kw:18} {n if n else '-'}")

    print(f"\nHTML dumped to {OUT} (gitignored)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
