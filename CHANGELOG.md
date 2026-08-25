# Changelog

## 1.0.0
First release.

- City of Tampa CSS portal client — no browser, no captcha (`requests` +
  BeautifulSoup), so the image is tens of MB.
- Bill PDF parser: water tiers, base charges, Tampa Bay Water pass-through,
  utility tax, wastewater, solid waste, deposits, fees and late charges. Parsed
  by line **shape**, not by label, because Tampa varies the wording. Verified by
  reconciling every bill against its own printed "Amount Now Due" — 11/11 to the
  penny, including an account-opening bill with $185 of deposits/fees and two
  bills carrying late charges.
- Bills are joined to the consumption chart so every service window uses the real
  **meter read** date rather than the bill date (they differ by ~5 days). Verified
  across 11 bills: consecutive windows chain with every boundary aligning exactly.
- Water Dashboard: `tampa_water:water_consumption` (gal) + `tampa_water:water_cost`,
  one water source. Per-tier costs as statistics only.
- Meter accuracy check against Flume / Flo via `recorder/statistics_during_period`
  over each bill's exact window.
- Sidebar dashboard: gallons per bill, stacked cost by service, cost by tier, the
  meter check, sortable archive, CSV export.
- Security from the start: ingress proven by source address (never a header),
  constant-time token compare, API port unpublished by default, docs disabled,
  CI actions pinned to SHAs, CodeQL, Dependabot, and a pre-commit/CI scan that
  blocks personal data.
