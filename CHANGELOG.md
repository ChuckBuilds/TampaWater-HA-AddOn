# Changelog

## 1.0.1
- **Fix: the add-on would not build.** The Dockerfile declared a build argument
  for its base image, and Supervisor substitutes its own base into that argument
  -- `ghcr.io/home-assistant/base`, which is Alpine and has no pip -- so the build
  died at `pip install` with "pip: not found". A `build.yaml` declaring
  `build_from` did not prevent it (this install goes through Supervisor's newer
  apps path, which appears not to honour it). The base is now pinned directly
  with no argument to substitute, and `build.yaml` is gone since it only existed
  to feed that argument.

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
- Each bill's exact service window (`period_start` / `period_end` / `service_days`
  / `meter_read`) is published as attributes, so a comparison against your own
  water sensors can be built in Home Assistant over the right days. The add-on
  does not perform that comparison itself.
- Sidebar dashboard: gallons per bill, stacked cost by service, cost by tier,
  sortable archive, CSV export.
- Security from the start: ingress proven by source address (never a header),
  constant-time token compare, API port unpublished by default, docs disabled,
  CI actions pinned to SHAs, CodeQL, Dependabot, and a pre-commit/CI scan that
  blocks personal data.
