# ❄️ Powder Alert

Get a notification on your phone and in your email when **both** of these are true:

1. **At least 6" of new snow** has fallen in the last 24 hours at a resort, and
2. **traffic is light**, so you aren't stuck on I‑70 while the powder gets tracked out.

It doesn't matter whether it's sunny, overcast or still snowing. Only the inches count.

| Alert | When it fires | Push priority |
|---|---|---|
| ✅ **GO NOW** | ≥ 6" new **and** drive time ≤ 1.25× free-flow and ≤ 15 min delay | Urgent |
| ❄️ **Powder day, traffic heavy** | ≥ 6" new but the roads are jammed. It tells you the best time to leave in the next 3 hours | High |
| 🌨️ **Heads-up** (evening) | ≥ 6" is *forecast* overnight, so you can plan an early start | Normal |

You get at most one alert per resort per level each day. If a "Powder, heavy traffic" resort later clears up, you get a single GO NOW upgrade.

---

## Where the data comes from

| Source | What it gives | Key needed? |
|---|---|---|
| **NRCS SNOTEL** | *Measured* hourly snow depth + snow-water equivalent at high stations next to each resort | No |
| **Resort snow reports** | The resort's own 24h study-plot number (optional scraper, set per resort) | No |
| **Open-Meteo** | Modeled snowfall for the past 24h and next 24h, downscaled to mid-mountain elevation | No |
| **NOAA / National Weather Service** | Official gridded snowfall forecast | No |
| **TomTom Traffic** | Live drive time, free-flow time, and *predicted* time for future departures | Free key (2,500 calls/day) |

**Which snow number wins:** the resort report comes first, then the SNOTEL measurement (averaged when a resort has several nearby stations), then the weather model as a last resort. SNOTEL "new snow" is the biggest rise in smoothed depth over the window. That way snow that has already started to settle still counts, and the ±1" sensor jitter doesn't.

### Resorts and regions
Resorts are grouped into **regions**. Each region has its own home base for the traffic check and its own fallback traffic pattern.

| Region | Drive from | Resorts (nearest SNOTEL stations) |
|---|---|---|
| **Colorado · I‑70** | Denver | Loveland (Loveland Basin), Arapahoe Basin & Keystone (Grizzly Peak), Breckenridge (Hoosier Pass), Copper (Copper Mtn + Fremont Pass), Vail (Vail Mtn), Winter Park (Berthoud Summit), Eldora (Lake Eldora) |
| **Utah · Wasatch Front** | Salt Lake City | **Little Cottonwood:** Alta & Snowbird (Atwater + Snowbird) · **Big Cottonwood:** Brighton (Brighton), Solitude (Mill‑D North + Brighton) · **Park City:** Park City Mountain & Deer Valley (Thaynes Canyon), Woodward Park City (Parleys Summit) · **Ogden Valley:** Snowbasin, Powder Mountain, Nordic Valley (Ben Lomond Peak) · **Provo Canyon:** Sundance (Timpanogos Divide) |

- **Change the Utah starting point** to wherever you stay by editing `regions.utah.home`.
- **Only want alerts for where you are this week?** Set `enabled: false` on the other region. Otherwise a Utah GO NOW will reach you in Denver too.
- **Add any other resort** with its coordinates, a `region`, and a nearby SNOTEL station ([CO list](https://wcc.sc.egov.usda.gov/nwcc/rgrpt?state=CO), [UT list](https://wcc.sc.egov.usda.gov/nwcc/rgrpt?state=UT)).
- **Cottonwood canyon closures:** Little and Big Cottonwood close for avalanche control on big storm mornings. TomTom's routing accounts for closures, so a closed canyon shows up as a drive 2.5× or more longer than normal. The alert then adds ⚠️ *possible road closure*. Check UDOT (udottraffic.utah.gov or the UDOT Cottonwoods account) before you go.
- Snowbasin has no SNOTEL station on the mountain. It borrows Ben Lomond Peak, about 15 miles north. Adding a resort `report:` scraper gives you Snowbasin's own number.

---

## Setup, simplest first

### Step 1: See it work (2 minutes, no accounts)
```bash
pip install -r requirements.txt
python -m powder_alert --demo        # sample alert + docs/index.html dashboard
python -m pytest -q                  # 22 offline tests
python -m powder_alert --force --dry-run   # REAL data right now, no notifications sent
```

### Step 2: Phone notifications (5 minutes, free)
1. Install the **ntfy** app ([iOS](https://apps.apple.com/app/ntfy/id1625396347) / [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)).
2. Subscribe to a hard-to-guess topic name, e.g. `kenny-powder-8f3k2q`. Anyone who knows the name can read it, so make it random.
3. `export NTFY_TOPIC=kenny-powder-8f3k2q` and run `python -m powder_alert --test-notify`.

### Step 3: Email notifications (5 minutes)
With Gmail, turn on 2‑Step Verification and create an **App Password** (Google Account → Security → App passwords).
```bash
export SMTP_USER=you@gmail.com SMTP_PASSWORD='abcd efgh ijkl mnop' EMAIL_TO=you@gmail.com
python -m powder_alert --test-notify
```
Want a text message? Put your carrier's email-to-SMS address in `EMAIL_TO` (comma-separate several). ntfy push is more reliable, though.

### Step 4: Real traffic (5 minutes, free)
Sign up at [developer.tomtom.com](https://developer.tomtom.com/) and copy your API key. Then `export TOMTOM_API_KEY=...`.
Without a key the app still runs, using a rough I‑70 weekday/weekend traffic model that is clearly labeled as an "estimate".

### Step 5: Run automatically in the cloud (10 minutes, free)
1. Create a **private** GitHub repo and push this folder to it.
2. Go to repo → Settings → Secrets and variables → Actions → **New repository secret** and add whichever of these you use:
   `NTFY_TOPIC`, `TOMTOM_API_KEY`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO` (optionally `PUSHOVER_TOKEN`, `PUSHOVER_USER`).
3. Go to Actions → **Powder Alert** → *Run workflow* with args `--test-notify` to confirm it works.

From then on it checks every 20 minutes from about 4–11 AM, plus once in the evening for the forecast heads-up. Your computer doesn't need to be on.

### Step 6: Online dashboard (optional)
Go to repo → Settings → Pages → Deploy from branch → `main` / `/docs`. Every run refreshes the dashboard at `https://<you>.github.io/<repo>/`. To get a tap-through link in every alert, put that URL in `notify.dashboard_url` in `config.yaml`.
(GitHub Pages on a private repo requires a paid plan. If you're on the free plan, make the repo public and keep all secrets in GitHub Secrets. Note that your state and dashboard files will then be public.)

---

## Tuning (`config.yaml`)
- `min_new_snow_in: 6`: your powder threshold.
- `lookback_hours: 24`: set to 48 to count two-day storm totals.
- `max_traffic_ratio` / `max_traffic_delay_min`: how "light" traffic must be.
- `morning_window`, `evening_forecast_time`: when alerts may fire.
- `regions.*.home`: where you drive from in each region; `regions.*.enabled` turns a whole region on or off.
- Per resort `report:` add a resort snow-report URL plus a regex that captures the 24h inches. There's an example under Eldora. Resort websites change often, so if the scraper breaks the app falls back to SNOTEL automatically.

## Files
```
config.yaml                 resorts, rules, channels
powder_alert/snow.py        SNOTEL, Open-Meteo, NWS, resort-report sources
powder_alert/traffic.py     TomTom live + predictive routing, fallback I-70 model
powder_alert/engine.py      the GO NOW / POWDER / HEADS-UP decision
powder_alert/notify.py      ntfy, email, Pushover, console
powder_alert/dashboard.py   docs/index.html + docs/latest.json
.github/workflows/          the free cloud schedule
tests/                      offline tests
```

## Limits worth knowing
- SNOTEL stations sit near resorts, not on them, so their numbers can differ from the resort's report by a few inches. Add a resort `report:` scraper when you want the resort's own number.
- GitHub's scheduler can run a few minutes late at busy times.
- The traffic check covers the drive **to** the mountain. The drive home isn't factored in.
