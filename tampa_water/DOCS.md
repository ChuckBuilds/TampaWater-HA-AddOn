# Tampa Water

Pulls City of Tampa water, wastewater and solid-waste billing into Home Assistant.

## Setup

1. **Configuration** tab:
   - **tampa_user** / **tampa_pass** — your `utilities.tampagov.net` login
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
| `sensor.tampa_water_last_bill_usage` | billed gallons — see attributes below |
| `sensor.tampa_water_last_bill_cost` | water portion only |
| `sensor.tampa_water_cost_per_1000_gal` | effective blended rate |
| `sensor.tampa_water_tier_<n>_cost` | cost in each conservation tier |
| `sensor.tampa_wastewater_cost` | wastewater (attribute: billed CCF) |
| `sensor.tampa_solid_waste_cost` | flat monthly trash charge |
| `sensor.tampa_water_sewer_cap` | Sewer Max / Lawn Credit, in CCF |
| `sensor.tampa_water_bill_date` | bill date |

Per-tier costs are published as **statistics only**, never as extra water
sources — Home Assistant sums the volume of every water source, so a source per
tier would multiply your gallons.

## Comparing against your own water sensors

This add-on does not compare anything; that belongs in Home Assistant. What it
gives you is the billed number plus the exact window it covers, on
`sensor.tampa_water_last_bill_usage`:

| Attribute | |
|---|---|
| `period_start` | first day of the service period |
| `period_end` | the day the meter was actually read |
| `service_days` | length of the period |
| `ccf` | billed CCF (1 CCF = 748 gal) |
| `meter_read` | the register reading at `period_end` |

**Use those dates.** Billing periods do not follow calendar months — a bill might
run `2026-06-22 → 2026-07-23` — so summing another sensor over "July" would be
wrong by a week of usage.

Note also that Home Assistant statistics buckets are cumulative: consumption over
a window is the value at `period_end` minus the value at the bucket *before*
`period_start`. Subtracting the first in-window bucket instead silently drops that
day, which is about 3% over a month.

**Do not read the wastewater/water gap as a meter error.** Tampa caps sewer volume
at the *Sewer Max / Lawn Credit* (the third-lowest monthly usage over 24 months),
so irrigation is billed water but not sewer. `sensor.tampa_water_sewer_cap` shows
the cap.

## Notes

- Monthly resolution is all Tampa publishes; bills arrive about a month behind.
- **Stormwater is not on this bill** — Tampa collects it via the county property
  tax bill.
- The archive in `/data/cache` is never purged, so history grows past the ~11
  months the portal keeps. Back up the add-on to preserve it.
