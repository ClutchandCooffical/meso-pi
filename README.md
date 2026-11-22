# meso-pi v1.0.2

Submit your weather station's readings to the **Midwest Meso Reporting** app
(https://weather-reporter-midwestmesoproject.replit.app/) automatically,
from a Raspberry Pi or any Linux box. No browser needed.

It uses the same API the Meso app's own form uses. The app publishes no API
documentation; these endpoints were identified from the app's own JavaScript
bundle on Nov 21, 2026, and the read path was verified live:

- `POST /api/wxids/{wxid}/unlock` with `{"password": "..."}` — unlocks your
  station for 30 minutes of activity (sets a session cookie).
- `POST /api/weather` with a JSON observation — records a reading.
- `GET /api/weather?wxid={wxid}` — lists a station's records (public).

## What it does

Four times a day, `meso-submit.py` pulls fresh readings from your weather
station, builds a Meso observation, and submits it — once after each
wx-brief publish (05:00, 10:00, 15:00, 20:00 local), at 12 minutes past
the hour. While severe weather mode is active it submits hourly instead.
It never submits more than once per `MIN_INTERVAL_MINUTES` (default 50),
so it never spams the project. It stays silent on success and fails
loudly (with the reason) when something is wrong.

What it sends, every time: temperature, humidity, wind speed and direction,
barometric pressure, weather conditions, today's rainfall total and the
rainfall-duration option closest to elapsed time since midnight, your fixed
GPS coordinates, your notes text, and the current time. It never uses the
form's prefilled defaults and never guesses a value.

## What it does not do

- It does not replace the Meso website. You still need your WXID and station
  password from the Meso group, and you can always check your records there.
- It does not write weather briefs. Severe mode comes from the SPC outlook
  and NWS alerts (see "Severe weather" below), not from a brief.
- It does not share anything. Your password stays on your machine, in a file
  only you can read, and the package never prints it.

## Requirements

- A Raspberry Pi (or any Linux box) with Python 3.8 or newer. Standard
  library only, no extra packages to install.
- Internet access to your station and to the Meso app.
- A weather station that can provide current readings in one of these ways:
  - a WeeWX homepage using the Belchertown skin (the same pages
    `wx-brief-pi` reads), or
  - a JSON file your station software writes, or
  - readings passed on the command line (for testing or for a wrapper script).
- Your WXID (e.g. `MMP-MO-N7`) and your Meso station password.

## Install

One line, straight from GitHub:

```sh
curl -fsSL https://raw.githubusercontent.com/ClutchandCooffical/meso-pi/main/install-remote.sh | bash
```

That downloads the latest meso-pi, extracts it, and runs the installer
into `/home/pi/meso`. For another directory, pass it as an argument:

```sh
curl -fsSL https://raw.githubusercontent.com/ClutchandCooffical/meso-pi/main/install-remote.sh | bash -s -- /opt/meso
```

Or install from a downloaded tarball:

```sh
tar xzf meso-pi-v1.0.2.tar.gz
cd meso-pi-v1.0.2
./install.sh            # installs to /home/pi/meso
./install.sh /opt/meso  # or another directory
```

(If you cloned the repo instead of using the tarball, run the installer
with `bash install.sh`: the executable bit does not survive a git clone
or a GitHub archive download. The installer itself makes the installed
scripts executable.)

The installer copies the scripts, creates `meso.conf` from the example
(`LIVE_SUBMIT=false`, shadow mode), and installs the cron entries: four
brief-time runs a day (05:12, 10:12, 15:12, 20:12 local), an hourly
severe-mode run, and a 30-minute severe-mode watchdog. It never
overwrites an existing `meso.conf`; on an upgrade it appends any new
settings that are missing.

## Configure

Edit `meso.conf` and fill in your own values:

| Setting | What to put |
|---|---|
| `WXID` | Your station ID, e.g. `MMP-MO-N7`. It is in the "Reporting WXID" dropdown on the Meso app. |
| `SOURCE` | `weewx`, `json`, or `args` (see below). |
| `STATION_URL` | Your WeeWX homepage URL (SOURCE=weewx). |
| `STATION_JSON` | Path to a JSON file with readings (SOURCE=json). |
| `LATITUDE`, `LONGITUDE` | Your station's fixed GPS coordinates. |
| `NOTES` | The notes text posted with every reading. Use your own attribution, e.g. `data gathered and entered from https://your-station.example.com by Your Name`. |
| `PASSWORD_FILE` | Where the station password lives. Leave the default. |
| `LIVE_SUBMIT` | `false` until you have tested (shadow mode). Flip to `true` for cutover. |
| `MIN_INTERVAL_MINUTES` | Minimum minutes between submissions. Default 50. |
| `SEVERE_STATE_FILE` | Path to a JSON file with `{"active": true/false}` that says whether severe weather mode is on. Default is the standard wx-brief-pi location. Checked alongside meso-pi's own `state/severe-mode.json`; either one saying active is enough. Leave blank to rely only on meso-pi's own check. |
| `ALERT_ZONE` | NWS county alert zone for the independent severe check, e.g. `MOC083`. Leave blank to derive it from `LATITUDE`/`LONGITUDE` via api.weather.gov/points/ (cached in `state/nws-zone.json`). |
| `MESO_BASE_URL` | The Meso app address. Change only if the app moves. |

Then set the password (it is read silently, never echoed, stored mode 0600):

```sh
./set-password.sh
```

### Station sources

**weewx (default).** Scrapes a WeeWX Belchertown homepage for temperature,
condition text, wind direction and speed, pressure, humidity, and today's
rain total. The fetch tolerates truncated bodies (some hosts declare a
Content-Length larger than the body they send) by retrying up to 5 times.
If your station uses a different WeeWX skin, the anchors will not match;
use the JSON source instead.

**json.** Your station software (or a small script) writes a JSON file with
these keys. `temp`, `humidity`, and `wspd` are required numbers;
`wdir`, `pressure`, `rain`, and `cond` are optional.

```json
{
  "temp": 67.1,
  "humidity": 62,
  "wspd": 2,
  "wdir": "ESE",
  "pressure": 29.02,
  "rain": 0.0,
  "cond": "Cloudy"
}
```

**args.** Pass the readings on the command line. Useful for testing and for
members whose station software can call a script:

```sh
python3 scripts/meso-submit.py --temp 67.1 --humidity 62 --wspd 2 \
  --wdir ESE --pressure 29.02 --cond Cloudy --rain 0
```

### How readings are mapped

- **Conditions.** Your station's free-form condition text is mapped onto the
  Meso dropdown: Clear, Sunny, Partly Cloudy, Cloudy, Overcast, Rainy,
  Stormy, Snowy, Foggy, Windy. Thunder or lightning becomes Stormy, rain or
  showers become Rainy, fog or mist becomes Foggy, overcast stays Overcast,
  and anything unrecognised becomes Cloudy.
- **Wind direction.** A 16-point compass label (N, NNE, NE, ...) is sent as
  is. Calm (0 mph, or a `--`/empty direction) sends a null direction, which
  is what the web form sends when its dropdown has no Calm option.
- **Rainfall duration.** The option closest to elapsed time since midnight
  local time: 1, 5, 12, or 24 hours. The Meso form requires rainfall amount
  and duration together, so both are always sent (amount 0.0 on a dry day).
- **Time.** The current time is sent as UTC ISO 8601; the server stores it
  as UTC.

## Test in shadow mode, then cut over

Shadow mode is the default (`LIVE_SUBMIT=false`). Nothing is submitted and
the Meso API is never contacted except by `meso-check.py`.

```sh
python3 scripts/meso-check.py
# checks the config, reads the station, and shows your newest Meso record

python3 scripts/meso-submit.py --dry-run
# builds the payload, prints it, writes shadow/last-dry-run.json
```

Run the dry run a few times across different conditions (calm wind, rain,
different times of day) and check the payload looks right: temperature and
humidity sane, wind direction a compass label or null, conditions one of the
ten options, rainfall duration sensible for the time of day.

When the dry runs look right:

1. Set `LIVE_SUBMIT=true` in `meso.conf`.
2. Run one live test: `python3 scripts/meso-submit.py --force`
3. Check the Meso app's Records table for your WXID. The new record should
   be the top row, with your values.
4. Leave the cron entries running. They submit four times a day, hourly
   while severe mode is active, at most once per 50 minutes, and stay
   silent on success.

To go back to shadow mode at any time, set `LIVE_SUBMIT=false` again. The
cron entry keeps running but submits nothing.

## Severe weather

The normal cadence is four submissions a day, after the 05:00, 10:00,
15:00, and 20:00 briefs. While severe weather mode is active, an hourly
cron entry also runs `meso-submit.py --require-severe`, which submits
once an hour.

Severe mode is checked two ways, and either one saying active is enough:

1. **wx-brief-pi's state file** (`SEVERE_STATE_FILE`, default
   `/home/pi/wx-brief/state/severe-mode-state.json`), when you run
   wx-brief-pi.
2. **meso-pi's own check.** Every 30 minutes, `meso-severe-check.py`
   reads the SPC Day 1 Convective Outlook (exact point-in-polygon for
   your station GPS) and the NWS alerts for your county zone, and writes
   `state/severe-mode.json`. It activates on SPC categorical risk
   Marginal or higher, any tornado/wind/hail probability contour
   containing the station, or any active severe thunderstorm / tornado /
   flash flood watch or warning. The alert zone is derived from your GPS
   via api.weather.gov/points/ (cached in `state/nws-zone.json`), or
   taken from `ALERT_ZONE` when set. A failed SPC read never changes the
   state, and a station within 0.25 degrees of a contour is flagged as
   edge-uncertain rather than guessed.

So a station without wx-brief-pi still gets hourly submissions in severe
weather, from meso-pi's own check. The 50-minute minimum interval still
applies unless you pass `--force`, so do not lower
`MIN_INTERVAL_MINUTES` below 50 without checking with the Meso group
first.

## Files

| File | What it is |
|---|---|
| `scripts/meso-submit.py` | The submitter. Run by cron, or by hand. |
| `scripts/meso-check.py` | Read-only setup check (config, station, API, password file). |
| `scripts/meso-severe-check.py` | Severe-mode watchdog. Every 30 min, checks SPC + NWS alerts and writes `state/severe-mode.json`. |
| `scripts/meso_spc.py` | SPC Day 1 areal-outline parser and point-in-polygon test. |
| `scripts/mesocommon.py` | Config, station readers, mapping, Meso API client, NWS zone/alerts. |
| `set-password.sh` | Stores the station password (mode 0600, never echoed). |
| `meso.conf` | Your configuration (created by install.sh, never overwritten). |
| `meso.conf.example` | The config template with every setting documented. |
| `meso.cron` | The cron entries installed by install.sh (4 brief-time runs a day, an hourly severe-mode run, and a 30-minute severe-mode watchdog). |
| `state/meso-last.json` | Watermark: last successful submission time and values. |
| `state/severe-mode.json` | Severe-mode state from meso-severe-check.py. |
| `state/nws-zone.json` | Cached NWS alert zone derived from the station GPS. |
| `shadow/last-dry-run.json` | The most recent dry-run payload. |
| `log/meso-submit.log` | Cron output from meso-submit.py. |
| `log/meso-severe-check.log` | Cron output from meso-severe-check.py. |

## Troubleshooting

- **"WXID is not set" / "LATITUDE/LONGITUDE are not set"** — fill in
  `meso.conf`. Run `meso-check.py` to verify.
- **"could not read the station"** — for `weewx`, check `STATION_URL` loads
  in a browser and is a Belchertown homepage. For `json`, check the file
  exists and has `temp`, `humidity`, and `wspd`. For `args`, pass the
  required flags.
- **"station reading failed sanity checks"** — a value is out of range
  (temperature -30 to 120F, pressure 20 to 35 inHg, humidity 0-100%,
  wind 0-120 mph, rain 0-20 in). Check the station.
- **"no station password found"** — run `./set-password.sh`.
- **"station unlock failed"** — the password does not match the WXID. Run
  `./set-password.sh` again with the correct password. The saved password is
  never printed, so a typo is invisible; re-entering it is the fix. The
  script never retries a rejected password on its own.
- **"submission failed"** — the message names the HTTP status and the
  server's error text. Check the Meso app loads in a browser; if it does,
  the payload may have a bad value (the log line shows the payload fields
  except the password).
- **A submission is missing from the Records table** — check
  `log/meso-submit.log` for the run, and `state/meso-last.json` for the
  last success. Runs that land inside the 50-minute interval skip quietly
  by design; that is not a failure.
- **The Meso app is down or moves** — the submit fails loudly and the next
  scheduled run retries. If the app moves to a new address, update
  `MESO_BASE_URL` in `meso.conf`.

## Security

- The station password lives in `.meso-password` in the install directory,
  mode 0600, readable only by the owner. Never copy that file to anyone,
  never paste the password into chat or email, and never commit it to git.
- The package never prints the password, in any log or message.
- Submissions go to the Meso app over HTTPS only.
- The API has no per-user token; the station password is the credential.
  If you suspect it leaked, change it on the Meso app (there is a password
  change on the station settings page) and re-run `./set-password.sh`.

- **"SEVERE CHECK: SPC unverified"** — the SPC outlook could not be read,
  so the severe state was left unchanged. Check that spc.noaa.gov loads.
  The next 30-minute run retries.
- **"SEVERE CHECK: NWS alerts fetch failed"** — the alerts fetch failed,
  so the SPC result alone decided. If it keeps failing, check that
  api.weather.gov loads, and that `ALERT_ZONE` is right (or blank, to
  derive it from the GPS).

## Version history

- **v1.0.2 (Nov 21, 2026)** — Independent severe check. meso-pi no longer
  depends on wx-brief-pi for severe mode: `meso-severe-check.py` runs
  every 30 minutes, reads the SPC Day 1 outlook (exact point-in-polygon)
  and the NWS alerts for the station's county zone (derived from the GPS
  via api.weather.gov/points/, cached, or `ALERT_ZONE` when set), and
  writes `state/severe-mode.json`. The hourly `--require-severe` run
  submits when either that file or wx-brief-pi's state file says severe
  mode is active. New `scripts/meso-spc.py` (SPC parser, ported from
  wx-brief-pi). The installer appends `ALERT_ZONE` to an existing
  meso.conf and strips both meso cron entries on reinstall.
- **v1.0.1 (Nov 21, 2026)** — Cadence fix. v1.0.0 submitted once an hour,
  every hour. v1.0.1 submits four times a day (after the 05:00, 10:00,
  15:00, and 20:00 briefs), plus hourly while severe weather mode is
  active (read from wx-brief-pi's severe-mode state file, new
  `--require-severe` flag and `SEVERE_STATE_FILE` setting). The
  installer appends `SEVERE_STATE_FILE` to an existing meso.conf.
- **v1.0.0 (Nov 21, 2026)** — First release. API identified from the app's
  own JavaScript; read path verified live; unlock + submit flow specified
  from the app's code and tested end to end on the author's station.
  Shadow mode, three station sources, 50-minute dedupe, hourly cron.

## License

MIT License. See [LICENSE](LICENSE).
