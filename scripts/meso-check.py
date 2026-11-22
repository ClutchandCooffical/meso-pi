#!/usr/bin/env python3
"""Verify a meso-pi setup without submitting anything.

Usage:
  python3 meso-check.py

Checks, in order:
  1. meso.conf loads and WXID / LATITUDE / LONGITUDE are set.
  2. The station can be read from the configured SOURCE.
  3. The Meso API is reachable: GET /api/weather?wxid=... lists the
     station's records, and the newest record is printed.
  4. Whether a station password file exists (its value is never read
     or printed here; unlocking is only tested by meso-submit.py).

Read-only. No password is used and nothing is submitted.
Exit codes: 0 all checks pass, 1 a check failed.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mesocommon as mc


def main():
    cfg = mc.load_config()
    ok = True

    print("meso-pi check")
    print(f"  install: {mc.HOME}")
    print(f"  config:  {mc.CONF_FILE}")
    print(f"  Meso:    {cfg['MESO_BASE_URL']}")
    print(f"  WXID:    {cfg['WXID'] or '(not set)'}")
    print(f"  source:  {cfg['SOURCE']}")
    print(f"  live submit: {cfg['LIVE_SUBMIT']}")
    print(f"  severe state file: {cfg.get('SEVERE_STATE_FILE') or '(not set)'}")
    print(f"  severe mode active: {mc.severe_mode_active(cfg)}")
    print()

    if not cfg["WXID"]:
        print("FAIL: WXID is not set in meso.conf.")
        return 1
    try:
        lat, lon = float(cfg["LATITUDE"]), float(cfg["LONGITUDE"])
    except (ValueError, TypeError):
        print("FAIL: LATITUDE/LONGITUDE are not set to numbers in meso.conf.")
        return 1
    print(f"  GPS: {lat}, {lon}")
    print("PASS: config loads, WXID and GPS are set.")
    print()

    # Station read.
    try:
        if cfg["SOURCE"].lower() == "weewx":
            st = mc.station_read_weewx(cfg["STATION_URL"])
        elif cfg["SOURCE"].lower() == "json":
            st = mc.station_read_json(cfg["STATION_JSON"])
        else:
            print(f"NOTE: SOURCE={cfg['SOURCE']!r} needs command-line "
                  "readings; skipping the station read.")
            st = None
    except Exception as e:
        print(f"FAIL: station read failed: {e}")
        return 1
    if st is not None:
        problems = mc.station_sanity(st)
        if problems:
            print("FAIL: station reading failed sanity checks: "
                  + "; ".join(problems))
            return 1
        print(f"  station now: {st['temp']}F, {st['cond']}, "
              f"{st['humidity']}% humidity, {st['wspd']} mph "
              f"({st['wdir'] or 'calm'}), {st['pressure']} inHg, "
              f"{st['rain']} in rain today")
        print("PASS: station reads and passes sanity checks.")
    print()

    # Meso API read.
    try:
        client = mc.MesoClient(cfg["MESO_BASE_URL"])
        recs = client.records(cfg["WXID"], limit=1)
    except Exception as e:
        print(f"FAIL: Meso API read failed: {e}")
        return 1
    if recs:
        r = recs[0]
        print(f"  newest Meso record for {cfg['WXID']}: "
              f"{r.get('temperature')}F, {r.get('conditions')}, "
              f"{r.get('humidity')}% humidity, "
              f"recorded {r.get('recordedAt')}")
    else:
        print(f"  no Meso records found yet for {cfg['WXID']}.")
    print("PASS: Meso API is reachable and returns records.")
    print()

    # Password file presence (never read the value here).
    if os.path.exists(cfg["PASSWORD_FILE"]):
        mode = oct(os.stat(cfg["PASSWORD_FILE"]).st_mode & 0o777)
        print(f"  password file exists at {cfg['PASSWORD_FILE']} "
              f"(mode {mode}; should be 0o600).")
        if mode != "0o600":
            print("  WARNING: fix the permissions: "
                  f"chmod 600 {cfg['PASSWORD_FILE']}")
    else:
        print(f"  no password file yet at {cfg['PASSWORD_FILE']}.")
        print("  Run ./set-password.sh before the first live submission.")
    print()
    print("All checks passed." if ok else "Some checks failed.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
