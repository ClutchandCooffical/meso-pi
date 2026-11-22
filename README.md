# meso-pi v1.0.0

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

Every hour, `meso-submit.py` pulls fresh readings from your weather station,
builds a Meso observation, and submits it — but only if at least
`MIN_INTERVAL_MINUTES` (default 50) have passed since the last successful
submission, so it never spams the project. It stays silent on success and
fails loudly (with the reason) when something is wrong.

What it sends, every time: temperature, humidity, wind speed and direction,
barometric pressure, weather conditions, today's rainfall total and the
rainfall-duration option closest to elapsed time since midnight, your fixed
GPS coordinates, your notes text, and the current time. It never uses the
form's prefilled defaults and never guesses a value.

## What it does not do

- It does not replace the Meso website. You still need your WXID and station
  password from the Meso group, and you can always check your records there.
- It does not read SPC outlooks or NWS alerts. If you want hourly submissions
  during severe weather, run it more often (see "Severe weather" below).
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

```sh
tar xzf meso-pi-v1.0.0.tar.gz
cd meso-pi-v1.0.0
./install.sh            # installs to /home/pi/meso
./install.sh /opt/meso  # or another directory
```

The installer copies the scripts, creates `meso.conf` from the example
(`LIVE_SUBMIT=false`, shadow mode), and installs an hourly cron entry that
runs `meso-submit.py` at 12 minutes past each hour. It never overwrites an
existing `meso.conf`.

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
4. Leave the hourly cron running. It submits at most once per 50 minutes
   and stays silent on success.

To go back to shadow mode at any time, set `LIVE_SUBMIT=false` again. The
cron entry keeps running but submits nothing.

## Severe weather

By default the package submits at most once an hour (cron at :12, 50-minute
minimum interval). During severe weather, if you want readings more often,
add a second cron line that runs every 30 minutes, or run
`meso-submit.py` by hand. The 50-minute minimum interval still applies
unless you pass `--force`, so do not lower `MIN_INTERVAL_MINUTES` below 50
without checking with the Meso group first.

## Files

| File | What it is |
|---|---|
| `scripts/meso-submit.py` | The submitter. Run by cron, or by hand. |
| `scripts/meso-check.py` | Read-only setup check (config, station, API, password file). |
| `scripts/mesocommon.py` | Config, station readers, mapping, Meso API client. |
| `set-password.sh` | Stores the station password (mode 0600, never echoed). |
| `meso.conf` | Your configuration (created by install.sh, never overwritten). |
| `meso.conf.example` | The config template with every setting documented. |
| `meso.cron` | The hourly cron entry installed by install.sh. |
| `state/meso-last.json` | Watermark: last successful submission time and values. |
| `shadow/last-dry-run.json` | The most recent dry-run payload. |
| `log/meso-submit.log` | Cron output from meso-submit.py. |

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
  hourly run retries. If the app moves to a new address, update
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

## Version history

- **v1.0.0 (Nov 21, 2026)** — First release. API identified from the app's
  own JavaScript; read path verified live; unlock + submit flow specified
  from the app's code and tested end to end on the author's station.
  Shadow mode, three station sources, 50-minute dedupe, hourly cron.

## License

MIT License. See [LICENSE](LICENSE).
