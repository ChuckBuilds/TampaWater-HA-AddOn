# Tampa Water ↔ Home Assistant

A **Home Assistant add-on** that brings **City of Tampa** water, wastewater and
solid-waste billing into Home Assistant — billed gallons and cost on the **Water
Dashboard**, a **per-tier** cost breakdown, a sidebar dashboard, and a
never-purged bill archive.

## Why

Tampa reads water meters **monthly**, so this is not a usage monitor — a
dedicated flow sensor will always tell you more about *when* water was used. What
only the utility has is the **billed** number: the meter of record, and what it
costs. This add-on brings that in so you can:

- see money spent on water in the Water Dashboard, split by conservation tier
- track water / wastewater / solid waste over months and years
- reconcile your own water sensors against the meter of record

## Comparing against your own water sensors

This add-on deliberately does **not** compare anything — that belongs in Home
Assistant, alongside whatever water sensors you actually run. What it owes such a
comparison is the billed number and the exact window it covers, published as
attributes on `sensor.tampa_water_last_bill_usage`:

```yaml
state: 11220            # gallons billed
attributes:
  period_start: 2026-06-22   # first day of the service period
  period_end:   2026-07-23   # the day the meter was actually read
  service_days: 31
  ccf: 15
  meter_read: 3692
```

**Use those dates.** Billing periods do not follow calendar months, so summing
another sensor over "July" would be wrong by a week of usage.

> **Wastewater is capped.** Tampa bills sewer on a *Sewer Max / Lawn Credit* —
> the third-lowest monthly usage over 24 months. In irrigation months your sewer
> volume is deliberately far below your water volume. That gap is the tariff, not
> a meter discrepancy; `sensor.tampa_water_sewer_cap` exposes the cap so you can
> tell the two apart.

## What you get

**Water Dashboard** — one water source: `tampa_water:water_consumption` (gallons)
with `tampa_water:water_cost` attached.

**Statistics for trending** — per-tier cost, wastewater cost, solid-waste cost.
Per-tier costs are *statistics only*, never extra water sources: HA sums the
volume of every water source, so a source per tier would multiply your gallons.

**Sensors** — `tampa_water_amount_due`, `_last_bill_usage`, `_last_bill_cost`,
`_cost_per_1000_gal`, `_tier_<n>_cost`, `tampa_wastewater_cost`,
`tampa_solid_waste_cost`, `_sewer_cap`, `_bill_date`.

**Sidebar dashboard** — gallons per bill, stacked cost by service, cost by tier,
and a sortable archive with CSV export.

## Install

1. **Settings → Add-ons → Add-on Store → ⋮ → Repositories**, add this repo URL.
2. Install **Tampa Water**.
3. **Configuration** tab → your `utilities.tampagov.net` username and password.
4. **Start**, then open the **Tampa Water** sidebar panel.

The bill only changes monthly, so the default poll is every 12 hours.

## How it works

No browser. `utilities.tampagov.net/css` is Cayenta Customer Self Service —
Java/Spring, a plain form login with a `_csrf` token, and no captcha — so this is
`requests` + BeautifulSoup, and the image is tens of MB rather than the ~1 GB a
Chromium-based scraper needs.

The portal splits the data, and both halves are needed:

| Source | Gives |
|---|---|
| `/css/utility/consumptionChart/<loc>/2` | read date, bill date, days, meter read, CCF |
| `/css/account/accountTransaction` | transactions, and links to the bill PDFs |
| bill PDF | the itemised charges — **the only source of the tier breakdown** |

The PDF carries the *bill* date but not the *read* date, and Tampa bills about
five days after reading. Bills are therefore joined to the consumption chart so
every published service window uses the real read date; a bill that cannot be
matched reports its window as unknown rather than stating the wrong dates.

Charges are parsed by **shape** (`<label> <qty> @ <rate> <amount>`) rather than by
matching labels, because Tampa varies the wording between bills. Every parsed
bill is reconciled against its own printed "Amount Now Due".

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r tampa_water/app/requirements.txt
python tests/test_parsers.py    # bill parsing + reconciliation
python tests/test_auth.py       # the API must not be reachable without a token
python scripts/check_no_pii.py  # also runs as a pre-commit hook
```

## Privacy & security

- Credentials live only in the add-on config; never logged, never committed.
- The bill archive stays in the add-on's `/data`; it is included in HA backups.
- `scripts/check_no_pii.py` blocks commits containing account-number-shaped digit
  runs, addresses, meter serials or emails, and prints findings redacted.
- The API port is **not published by default**; ingress is proven by the request
  source address, never by a header. See [`SECURITY.md`](SECURITY.md).

## Limitations

- Monthly resolution — that is all Tampa publishes. Use a dedicated flow
  sensor if you want to see usage as it happens.
- The bill arrives roughly a month in arrears.
- **Stormwater is not on this bill.** Tampa collects it as a non-ad valorem
  assessment on the county property tax bill.
- The portal keeps ~11 months of bills; the archive is never purged, so history
  grows past that from first run.

Not affiliated with the City of Tampa.
