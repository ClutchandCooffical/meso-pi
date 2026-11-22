#!/usr/bin/env python3
"""Severe-weather mode watchdog for meso-pi.

Runs every 30 minutes from cron. Reads the SPC Day 1 outlook (exact
point-in-polygon for the station) and the NWS alerts for the station's
county zone, and writes state/severe-mode.json. The hourly
meso-submit.py --require-severe run only submits while this file says
severe mode is active, so a station without wx-brief-pi still gets
hourly submissions in severe weather.

Activation: SPC categorical risk Marginal or higher for the station, any
tornado/wind/hail probability contour containing the station, or any
active severe thunderstorm / tornado / flash flood watch or warning.

- On activation: writes state and logs ACTIVATED.
- While active: updates the threat text and logs ACTIVE.
- On deactivation: writes state and logs DEACTIVATED.
- No change: logs a quiet line and exits 0.

An SPC read that fails does NOT change the state: a tier never flips on
unverified data. Edge-uncertain readings (station within 0.25 degrees of
a contour) are logged as flags but do not by themselves activate.

The NWS alert zone is derived from the station GPS (api.weather.gov/
points/) and cached in state/nws-zone.json, or taken from ALERT_ZONE
in meso.conf when set. If the alerts fetch fails, the SPC result alone
decides; if both fail, the state is left unchanged.

Exit 0 always (a failed check is logged, not fatal to cron).
Standard library only.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mesocommon as mc
import meso_spc as SPC

CAT_ORDER = {"TSTM": 0, "MRGL": 1, "SLGT": 2, "ENH": 3, "MDT": 4, "HIGH": 5}
EDGE_UNCERTAIN_DEG = 0.25
SEVERE_EVENTS = ("Severe Thunderstorm Watch", "Tornado Watch",
                 "Flash Flood Watch", "Severe Thunderstorm Warning",
                 "Tornado Warning", "Flash Flood Warning")


def threat_summary(sp, alerts):
    """Return (threat_bool, description) for the current SPC + alerts."""
    reasons = []
    if sp.get("ok"):
        cat = sp.get("cat")
        if cat and CAT_ORDER.get(cat, 0) >= 1:
            reasons.append(f"SPC {cat}")
        for name in ("tornado", "wind", "hail"):
            if sp.get(name):
                reasons.append(f"SPC {name} {sp[name]}")
    watches = [a for a in alerts if a["event"] in SEVERE_EVENTS]
    for w in watches:
        reasons.append(f"NWS {w['event']}")
    return bool(reasons), "; ".join(reasons)


def main():
    cfg = mc.load_config()
    os.makedirs(mc.STATE_DIR, exist_ok=True)
    state_file = os.path.join(mc.STATE_DIR, "severe-mode.json")
    state = mc.read_json_file(state_file) or {"active": False, "since": None,
                                              "last_threat": None}

    sp = SPC.spc_status(cfg)
    alerts = []
    try:
        alerts = mc.nws_alerts(cfg)
    except Exception as e:
        mc.log(f"SEVERE CHECK: NWS alerts fetch failed: {e}")

    if not sp.get("ok"):
        mc.log(f"SEVERE CHECK: SPC unverified ({sp.get('error')}); "
               f"state unchanged (active={state['active']}).")
        return 0

    threat, desc = threat_summary(sp, alerts)
    edge_note = ""
    if sp.get("cat_edge_deg") is not None and sp["cat_edge_deg"] < EDGE_UNCERTAIN_DEG:
        edge_note = (f" [edge-uncertain: {sp['cat_edge_deg']:.2f} deg from "
                     f"contour, tier {sp.get('cat') or 'none'}]")

    now = time.time()
    if threat and not state["active"]:
        state = {"active": True, "since": now, "last_threat": desc,
                 "checked_at": now}
        mc.write_json_file(state_file, state)
        mc.log(f"SEVERE MODE ACTIVATED: {desc}{edge_note}")
        return 0

    if threat and state["active"]:
        state["last_threat"] = desc
        state["checked_at"] = now
        mc.write_json_file(state_file, state)
        mc.log(f"SEVERE MODE ACTIVE: {desc}{edge_note}")
        return 0

    if not threat and state["active"]:
        state = {"active": False, "since": None, "last_threat": None,
                 "checked_at": now}
        mc.write_json_file(state_file, state)
        mc.log("SEVERE MODE DEACTIVATED: no SPC risk or watch/warning for "
               "the station.")
        return 0

    state["checked_at"] = now
    mc.write_json_file(state_file, state)
    mc.log(f"SEVERE CHECK: no threat (SPC {sp.get('cat') or 'none'}, "
           f"{len(alerts)} alerts){edge_note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
