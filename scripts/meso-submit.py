#!/usr/bin/env python3
"""Submit a weather station reading to Midwest Meso Reporting.

Usage:
  python3 meso-submit.py                 (readings from meso.conf SOURCE)
  python3 meso-submit.py --dry-run       (build the payload, print it, submit nothing)
  python3 meso-submit.py --force         (ignore the minimum-interval dedupe)
  python3 meso-submit.py --temp 67.1 --humidity 62 --wspd 0 --pressure 29.02 \
      --cond Cloudy --rain 0             (SOURCE=args: readings on the command line)

Behaviour:
  - Loads meso.conf (WXID, station source, GPS, notes, LIVE_SUBMIT).
  - Pulls fresh station readings at run time; never uses stale or guessed
    values. Fails loudly if the station cannot be read.
  - Skips silently when the last successful submission is newer than
    MIN_INTERVAL_MINUTES (default 50), unless --force is given.
  - --require-severe: only submit when severe weather mode is active
    (per SEVERE_STATE_FILE, the wx-brief-pi severe-mode state). Used by
    the hourly cron entry; the four brief-time runs submit without it.
    Skips silently when severe mode is not active.
  - In shadow mode (LIVE_SUBMIT=false, the default) it builds the payload,
    writes it to shadow/last-dry-run.json, prints it, and submits nothing.
  - In live mode (LIVE_SUBMIT=true) it unlocks the WXID with the station
    password from the password file, then POSTs the reading to the Meso API.
    The password is never printed.
  - On a confirmed success it writes state/meso-last.json with the
    submission time and the values submitted.

Exit codes: 0 ok (including a dedupe skip or a dry run), 1 failure.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mesocommon as mc


def parse_args():
    p = argparse.ArgumentParser(description="Submit a Meso station reading.")
    p.add_argument("--dry-run", action="store_true",
                   help="build the payload and print it; submit nothing")
    p.add_argument("--force", action="store_true",
                   help="ignore the minimum-interval dedupe")
    p.add_argument("--require-severe", action="store_true",
                   help="only submit when severe mode is active "
                   "(per SEVERE_STATE_FILE); skip silently otherwise")
    p.add_argument("--temp", type=float, help="temperature F (SOURCE=args)")
    p.add_argument("--humidity", type=float, help="humidity %% (SOURCE=args)")
    p.add_argument("--wspd", type=float, help="wind speed mph (SOURCE=args)")
    p.add_argument("--wdir", default="", help="wind direction label (SOURCE=args)")
    p.add_argument("--pressure", type=float, help="pressure inHg (SOURCE=args)")
    p.add_argument("--cond", default="Cloudy", help="condition text (SOURCE=args)")
    p.add_argument("--rain", type=float, default=0.0,
                   help="rain total today in inches (SOURCE=args)")
    return p.parse_args()


def read_station(cfg, args):
    """Get fresh readings from the configured source."""
    source = cfg["SOURCE"].lower()
    if source == "args":
        missing = [k for k in ("temp", "humidity", "wspd", "pressure")
                   if getattr(args, k) is None]
        if missing:
            raise ValueError(
                f"SOURCE=args needs --{' --'.join(missing)} on the command line")
        return {
            "temp": args.temp, "temp_s": str(args.temp),
            "cond": args.cond, "wdir": args.wdir,
            "wspd": args.wspd, "wspd_s": str(args.wspd),
            "pressure": args.pressure, "pressure_s": str(args.pressure),
            "humidity": args.humidity, "humidity_s": str(args.humidity),
            "rain": args.rain, "rain_s": str(args.rain),
            "fetched_at": time.time(),
        }
    if source == "json":
        if not cfg["STATION_JSON"]:
            raise ValueError("SOURCE=json needs STATION_JSON set in meso.conf")
        return mc.station_read_json(cfg["STATION_JSON"])
    if source == "weewx":
        if not cfg["STATION_URL"]:
            raise ValueError("SOURCE=weewx needs STATION_URL set in meso.conf")
        return mc.station_read_weewx(cfg["STATION_URL"])
    raise ValueError(f"unknown SOURCE {cfg['SOURCE']!r} "
                     "(use weewx, json, or args)")


def main():
    args = parse_args()
    cfg = mc.load_config()
    os.makedirs(mc.STATE_DIR, exist_ok=True)
    os.makedirs(mc.SHADOW_DIR, exist_ok=True)

    if not cfg["WXID"]:
        mc.log("ERROR: WXID is not set in meso.conf. See the README.")
        return 1
    for key in ("LATITUDE", "LONGITUDE"):
        if not cfg[key]:
            mc.log(f"ERROR: {key} is not set in meso.conf. See the README.")
            return 1
    try:
        float(cfg["LATITUDE"])
        float(cfg["LONGITUDE"])
    except ValueError:
        mc.log("ERROR: LATITUDE/LONGITUDE must be numbers.")
        return 1

    # Severe-only runs (--require-severe, the hourly cron entry) submit
    # only while severe mode is active. Check before reading the station.
    if args.require_severe and not mc.severe_mode_active(cfg):
        mc.log("SKIP: --require-severe and severe mode is not active.")
        return 0

    # Dedupe: never submit more than once per MIN_INTERVAL_MINUTES.
    state = mc.read_json_file(cfg["STATE_FILE"], {}) or {}
    last_at = state.get("last_submission_epoch", 0)
    age_min = (time.time() - last_at) / 60.0 if last_at else 1e9
    if not args.force and age_min < cfg["MIN_INTERVAL_MINUTES"]:
        mc.log(f"SKIP: last submission {age_min:.0f} min ago "
               f"(minimum interval {cfg['MIN_INTERVAL_MINUTES']} min).")
        return 0

    try:
        st = read_station(cfg, args)
    except Exception as e:
        mc.log(f"ERROR: could not read the station: {e}")
        return 1
    problems = mc.station_sanity(st)
    if problems:
        mc.log("ERROR: station reading failed sanity checks: "
               + "; ".join(problems))
        return 1
    try:
        payload = mc.build_payload(cfg, st)
    except Exception as e:
        mc.log(f"ERROR: could not build the submission: {e}")
        return 1

    live = cfg["LIVE_SUBMIT"] and not args.dry_run
    if not live:
        out_path = os.path.join(mc.SHADOW_DIR, "last-dry-run.json")
        mc.write_json_file(out_path, {
            "at": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
            "live_submit": False,
            "payload": payload,
            "station": {k: st[k] for k in
                        ("temp", "humidity", "wspd", "wdir", "pressure",
                         "rain", "cond")},
        })
        mc.log("DRY RUN (LIVE_SUBMIT=false): nothing submitted.")
        mc.log(f"Payload written to {out_path}:")
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    password = mc.read_password(cfg["PASSWORD_FILE"])
    if not password:
        mc.log(f"ERROR: no station password found at {cfg['PASSWORD_FILE']}. "
               "Run ./set-password.sh first. See the README.")
        return 1

    client = mc.MesoClient(cfg["MESO_BASE_URL"])
    try:
        client.unlock(cfg["WXID"], password)
    except Exception as e:
        mc.log(f"ERROR: station unlock failed: {e}")
        mc.log("Check the password with ./set-password.sh. "
               "The saved password is never printed.")
        return 1
    try:
        result = client.submit(payload)
    except Exception as e:
        mc.log(f"ERROR: submission failed: {e}")
        return 1

    record_id = result.get("id") if isinstance(result, dict) else None
    mc.write_json_file(cfg["STATE_FILE"], {
        "last_submission": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "last_submission_local": time.strftime("%Y-%m-%d %H:%M %Z"),
        "last_submission_epoch": time.time(),
        "wxid": cfg["WXID"],
        "record_id": record_id,
        "values": payload,
    })
    mc.log(f"SUBMITTED OK: {payload['temperature']}F, "
           f"{payload['conditions']}, {payload['humidity']}% humidity, "
           f"{payload['windSpeed']} mph, {payload['barometricPressure']} inHg, "
           f"{payload['rainfallAmount']} in / {payload['rainfallDurationHours']} h"
           + (f" (record {record_id})" if record_id else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
