# Tampa Water

Pulls City of Tampa water, wastewater and solid-waste billing into Home Assistant.

## Setup

1. **Configuration** tab:
   - **tampa_user** / **tampa_pass** — your `utilities.tampagov.net` login
   - **compare_entities** — *optional.* Comma-separated statistic ids of other
     water meters to check against the bill, e.g.
     `sensor.flume_home_water, sensor.flo_total_consumption`. Leave empty to
     disable the accuracy check.
   - **backfill_bills** — how many bills to pull (default 24; the portal keeps ~11)
   - **poll_interval_hours** — default 12. The bill only changes monthly.
   - **auth_token** — only needed if you publish the API port (see Network tab)
2. **Start**, then open the **Tampa Water** sidebar panel.

> Credentials are read from `/data/options.json` at startup. After changing them,
> **restart the add-on**.

## Entities

**Water Dashboard** — `tampa_water:water_consumption` (gallons) with
`tampa_water:water_cost`. Add it under **Settings → Dashboards → Energy → Water**
if the add-on has not wired it automatically.

**Sensors**

| Entity | What |
|---|---|
| `sensor.tampa_water_amount_due` | total owed on the latest bill |
| `sensor.tampa_water_last_bill_usage` | billed gallons (attributes: CCF, days) |
| `sensor.tampa_water_last_bill_cost` | water portion only |
| `sensor.tampa_water_cost_per_1000_gal` | effective blended rate |
| `sensor.tampa_water_tier_<n>_cost` | cost in each conservation tier |
| `sensor.tampa_wastewater_cost` | wastewater (attribute: billed CCF) |
| `sensor.tampa_solid_waste_cost` | flat monthly trash charge |
| `sensor.tampa_water_sewer_cap` | Sewer Max / Lawn Credit, in CCF |
| `sensor.tampa_water_vs_<entity>` | % variance vs each comparison entity |

Per-tier costs are published as **statistics only**, never as extra water
sources — HA sums the volume of every water source, so a source per tier would
multiply your gallons.

## Reading the accuracy check

Each comparison entity is summed over the bill's **exact service window** (meter
read to meter read), because billing periods do not follow calendar months.

A few percent either way is normal — a clamp-on or inline sensor estimates flow,
while the utility meter is a positive-displacement register. Consistent drift in
one direction, growing over several bills, is the signal worth acting on.

**Do not read the wastewater/water gap as an error.** Tampa caps sewer volume at
the *Sewer Max / Lawn Credit* (the third-lowest monthly usage over 24 months), so
irrigation is billed water but not sewer.

## Notes

- Monthly resolution is all Tampa publishes; bills arrive about a month behind.
- **Stormwater is not on this bill** — Tampa collects it via the county property
  tax bill.
- The archive in `/data/cache` is never purged, so history grows past the ~11
  months the portal keeps. Back up the add-on to preserve it.
