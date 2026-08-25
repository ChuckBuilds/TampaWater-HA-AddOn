"""
Auth regression tests. No network, no credentials.

The sibling TECO add-on shipped a bug where `_auth` exempted any request
carrying an `X-Ingress-Path` header, on the assumption that only Home Assistant
ingress sends one. Headers are attacker-controlled, so any client could send it
and read the whole billing archive. This add-on proves ingress by the request's
SOURCE ADDRESS instead, and these tests keep it that way.

    python tests/test_auth.py
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tampa_water", "app"))

TOKEN = "unit-test-token"
os.environ["SIDECAR_TOKEN"] = TOKEN
os.environ["CACHE_DIR"] = tempfile.mkdtemp(prefix="tampa-authtest-")

import server as S  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

S.app.router.lifespan_context = None       # never start the poll loop
INGRESS_HDR = {"X-Ingress-Path": "/api/hassio_ingress/abcdef"}

failures: list[str] = []


def check(label: str, got: int, want: int) -> None:
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label:56} -> {got} (want {want})")
    if not ok:
        failures.append(label)


def main() -> int:
    lan = TestClient(S.app, raise_server_exceptions=False)
    ingress = TestClient(S.app, client=("172.30.32.2", 5000),
                         raise_server_exceptions=False)

    print("token configured -- /export requires it")
    check("no token", lan.get("/export").status_code, 401)
    check("wrong token",
          lan.get("/export", headers={"X-Auth-Token": "wrong"}).status_code, 401)
    check("correct token",
          lan.get("/export", headers={"X-Auth-Token": TOKEN}).status_code, 200)

    # the bug this file exists for
    check("forged X-Ingress-Path from a LAN client",
          lan.get("/export", headers=INGRESS_HDR).status_code, 401)
    check("forged header + wrong token",
          lan.get("/export", headers={**INGRESS_HDR,
                                      "X-Auth-Token": "wrong"}).status_code, 401)
    check("spoofed X-Forwarded-For does not launder the client",
          lan.get("/export", headers={**INGRESS_HDR,
                                      "X-Forwarded-For": "172.30.32.2"}).status_code, 401)

    check("real ingress (supervisor IP + header)",
          ingress.get("/export", headers=INGRESS_HDR).status_code, 200)
    check("supervisor IP but no ingress header",
          ingress.get("/export").status_code, 401)

    # CSV export carries the same archive and must be gated identically
    check("csv export needs the token",
          lan.get("/export", params={"format": "csv"}).status_code, 401)

    check("interactive docs disabled", lan.get("/docs").status_code, 404)
    check("openapi schema disabled", lan.get("/openapi.json").status_code, 404)

    r = lan.get("/health")
    check("/health is open (no billing data)", r.status_code, 200)
    check("/health does not leak the token", 0 if TOKEN in r.text else 1, 1)

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        return 1
    print("all auth checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
