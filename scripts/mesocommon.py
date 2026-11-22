#!/usr/bin/env python3
"""Shared helpers for the meso-pi package.

Every script in this package imports mesocommon. It knows where the install
lives (the parent of the scripts/ directory), reads meso.conf, and provides
the station readers and the Midwest Meso Reporting API client.

Standard library only. No secrets are ever printed.

The Meso API was identified from the reporting app's own JavaScript bundle
(https://weather-reporter-midwestmesoproject.replit.app/assets/index-*.js,
read Nov 21, 2026). The app publishes no API docs; these endpoints are the
ones its own form uses:
  POST /api/wxids/{wxid}/unlock   {"password": "..."}  -> session cookie
  GET  /api/wxids/{wxid}/session  -> {"authenticated": true/false}
  POST /api/weather               JSON observation     -> created record
  GET  /api/weather?wxid={wxid}   -> records list (public, no auth)
"""
import http.client
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.dirname(SCRIPTS_DIR)
CONF_FILE = os.path.join(HOME, "meso.conf")
STATE_DIR = os.path.join(HOME, "state")
SHADOW_DIR = os.path.join(HOME, "shadow")
LOG_DIR = os.path.join(HOME, "log")

USER_AGENT = "meso-pi/1.0.2 (Midwest Meso Reporting submitter)"

# The Meso conditions dropdown. The station's free-form condition text is
# mapped onto one of these by map_condition().
MESO_CONDITIONS = ["Clear", "Sunny", "Partly Cloudy", "Cloudy", "Overcast",
                   "Rainy", "Stormy", "Snowy", "Foggy", "Windy"]

# 16-point compass labels the Meso wind-direction dropdown accepts.
COMPASS_16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]

RAINFALL_DURATION_OPTIONS = [1, 5, 12, 24]

DEFAULTS = {
    "MESO_BASE_URL": "https://weather-reporter-midwestmesoproject.replit.app",
    "WXID": "",
    "PASSWORD_FILE": os.path.join(HOME, ".meso-password"),
    "SOURCE": "weewx",
    "STATION_URL": "",
    "STATION_JSON": "",
    "LATITUDE": "",
    "LONGITUDE": "",
    "NOTES": "",
    "LIVE_SUBMIT": "false",
    "MIN_INTERVAL_MINUTES": "50",
    "STATE_FILE": os.path.join(STATE_DIR, "meso-last.json"),
    # Severe-mode state file. The default is the standard wx-brief-pi
    # location; the hourly --require-severe cron entry only submits when
    # this file says severe mode is active. Set it to another path, or
    # blank to disable severe-mode submissions, in meso.conf.
    "SEVERE_STATE_FILE": "/home/pi/wx-brief/state/severe-mode-state.json",
    # NWS alert zone for the severe check (e.g. MOC083). Blank means
    # derive it from LATITUDE/LONGITUDE via api.weather.gov/points/
    # (cached in state/nws-zone.json). Set it explicitly if the
    # derivation fails or you already know your zone.
    "ALERT_ZONE": "",
}


def load_config():
    """Read meso.conf (KEY=VALUE, # comments) over the defaults."""
    cfg = dict(DEFAULTS)
    try:
        with open(CONF_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                cfg[key.strip()] = val.strip().strip('"').strip("'")
    except OSError:
        pass
    cfg["LIVE_SUBMIT"] = cfg["LIVE_SUBMIT"].lower() in ("1", "true", "yes", "on")
    try:
        cfg["MIN_INTERVAL_MINUTES"] = int(cfg["MIN_INTERVAL_MINUTES"])
    except ValueError:
        cfg["MIN_INTERVAL_MINUTES"] = 50
    return cfg


def log(msg):
    """Print with a timestamp (cron captures stdout into the log files)."""
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S %Z')} {msg}", flush=True)


def read_json_file(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json_file(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def read_password(path):
    """Read the station password file. Returns None if missing/empty.

    The password value is never logged or printed.
    """
    try:
        with open(path) as f:
            pw = f.read().strip()
        return pw or None
    except OSError:
        return None


def severe_mode_active(cfg):
    """Is severe weather mode active?

    Checks two sources; either one saying active is enough:
      1. cfg["SEVERE_STATE_FILE"] — the wx-brief-pi severe-mode state
         (default: the standard wx-brief-pi location). A JSON file with
         {"active": true}.
      2. meso-pi's own state/severe-mode.json, written by
         meso-severe-check.py (the independent SPC + NWS alerts check).
    Anything else — missing file, bad JSON, blank path, active false —
    is not severe. Fails closed: when we cannot tell, we do not submit
    on the severe-only runs.
    """
    # Source 1: wx-brief-pi's state file.
    path = (cfg.get("SEVERE_STATE_FILE") or "").strip()
    if path:
        data = read_json_file(path)
        if isinstance(data, dict) and data.get("active") is True:
            return True
    # Source 2: meso-pi's own independent severe check.
    own = read_json_file(os.path.join(STATE_DIR, "severe-mode.json"))
    if isinstance(own, dict) and own.get("active") is True:
        return True
    return False


def fetch_text(url, timeout=25, attempts=3, pause=3):
    """GET a URL as text, retrying transient failures. Raises on failure."""
    last = "unknown"
    for _ in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(pause)
    raise RuntimeError(f"fetch failed ({url}): {last}")


def fetch_json(url, timeout=25, attempts=3, pause=3):
    """GET a URL and parse it as JSON. Raises on failure."""
    return json.loads(fetch_text(url, timeout=timeout, attempts=attempts,
                                 pause=pause))


def discover_zone(cfg):
    """Resolve the NWS county alert zone for the station GPS.

    Uses cfg["ALERT_ZONE"] when set. Otherwise derives it from
    LATITUDE/LONGITUDE via api.weather.gov/points/ (the county zone in
    the response properties) and caches it in state/nws-zone.json for
    7 days. Coordinates are rounded to 4 decimals: the NWS API rejects
    higher precision with a 301. Returns the zone id (e.g. "MOC083").
    Raises RuntimeError when it cannot be resolved.
    """
    override = (cfg.get("ALERT_ZONE") or "").strip()
    if override:
        return override
    cache = os.path.join(STATE_DIR, "nws-zone.json")
    cached = read_json_file(cache)
    if cached and time.time() - cached.get("at", 0) < 7 * 86400:
        zone = cached.get("zone")
        if zone:
            return zone
    try:
        lat = float(cfg["LATITUDE"])
        lon = float(cfg["LONGITUDE"])
    except (ValueError, TypeError):
        raise RuntimeError("LATITUDE/LONGITUDE must be numbers to "
                           "derive the NWS alert zone")
    url = (f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}")
    data = fetch_json(url, attempts=2)
    county = (data.get("properties") or {}).get("county") or ""
    zone = county.rstrip("/").split("/")[-1] if county else ""
    if not zone:
        raise RuntimeError("NWS /points/ returned no county zone")
    os.makedirs(STATE_DIR, exist_ok=True)
    write_json_file(cache, {"at": time.time(), "zone": zone})
    return zone


def nws_alerts(cfg):
    """Active NWS alerts for the station's county zone.

    Returns a list of dicts with event, severity, headline, ends.
    Raises RuntimeError when the zone cannot be resolved or the
    alerts fetch fails.
    """
    zone = discover_zone(cfg)
    url = f"https://api.weather.gov/alerts/active?zone={zone}"
    data = fetch_json(url, attempts=2)
    out = []
    for f in data.get("features", []):
        p = f["properties"]
        out.append({
            "event": p.get("event", ""),
            "severity": p.get("severity", ""),
            "headline": p.get("headline", ""),
            "ends": p.get("ends") or p.get("expires") or "",
        })
    return out


# ---------------------------------------------------------------- station

def _grab(pattern, html, name):
    m = re.search(pattern, html, re.S)
    if not m:
        raise ValueError(f"anchor not found: {name}")
    return m.group(1).strip()


def _read_tolerant(resp):
    """Read a body, tolerating a short Content-Length flake."""
    try:
        return resp.read()
    except http.client.IncompleteRead as e:
        return e.partial


def fetch_homepage(url, must_contain=None, attempts=5, pause=3):
    """Fetch a WeeWX homepage, retrying truncated-body failures.

    Some hosts intermittently declare a Content-Length larger than the body
    they send; the partial body then ends mid-<head> before the station
    anchors. Retrying reliably lands a full body.
    """
    last_note = "unknown"
    for _ in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=25) as resp:
                body = _read_tolerant(resp).decode("utf-8", errors="replace")
        except Exception as e:
            last_note = f"{type(e).__name__}"
            time.sleep(pause)
            continue
        if must_contain and must_contain not in body:
            last_note = f"truncated body (missing {must_contain!r})"
            time.sleep(pause)
            continue
        return body
    raise RuntimeError(f"homepage fetch failed after {attempts} attempts: {last_note}")


def station_read_weewx(url):
    """Scrape live station values off a WeeWX Belchertown homepage.

    Returns a dict with numeric values plus the raw display strings.
    Raises ValueError/RuntimeError on failure.
    """
    html = fetch_homepage(url, must_contain='class="outtemp"')
    temp_s = _grab(r'<span class="outtemp">\s*([^<]+?)\s*</span>', html, "temp")
    cond = _grab(r'<div class="current-obs-text">\s*([^<]+?)\s*</div>', html, "condition")
    wdir = _grab(r'<span class="curwinddir">\s*([^<]+?)\s*</span>', html, "wind dir")
    wspd_s = _grab(r'<span class="curwindspeed">\s*([^<]+?)\s*</span>', html, "wind speed")
    pres_s = _grab(r'<span class=pressure>\s*([^<]+?)\s*</span>', html, "pressure")
    hum_s = _grab(r'<span class=outHumidity>\s*([^<%]+)', html, "humidity")
    rain_s = _grab(r"<span class='dayRain'>\s*([^<]+?)\s*</span>", html, "rain")

    def num(s, name):
        try:
            return float(re.sub(r"[^0-9.\-]", "", s))
        except ValueError:
            raise ValueError(f"station value not numeric: {name}={s!r}")

    return {
        "temp": num(temp_s, "temp"),
        "temp_s": temp_s,
        "cond": cond,
        "wdir": wdir,
        "wspd": num(wspd_s, "wind speed"),
        "wspd_s": wspd_s,
        "pressure": num(pres_s, "pressure"),
        "pressure_s": pres_s.split()[0],
        "humidity": num(hum_s, "humidity"),
        "humidity_s": hum_s,
        "rain": num(rain_s, "rain"),
        "rain_s": rain_s.split()[0],
        "fetched_at": time.time(),
    }


def station_read_json(path):
    """Read station values from a JSON file.

    Expected keys (all numbers except cond/wdir, which are strings):
      temp, humidity, wspd, wdir, pressure, rain, cond
    Missing optional keys default sensibly; temp/humidity/wspd are required.
    """
    data = read_json_file(path)
    if not isinstance(data, dict):
        raise ValueError(f"station JSON is not an object: {path}")
    for key in ("temp", "humidity", "wspd"):
        if key not in data:
            raise ValueError(f"station JSON missing required key: {key}")
    return {
        "temp": float(data["temp"]),
        "temp_s": str(data.get("temp_s", data["temp"])),
        "cond": str(data.get("cond", "Cloudy")),
        "wdir": str(data.get("wdir", "")),
        "wspd": float(data["wspd"]),
        "wspd_s": str(data.get("wspd_s", data["wspd"])),
        "pressure": float(data.get("pressure", 29.92)),
        "pressure_s": str(data.get("pressure_s", data.get("pressure", 29.92))),
        "humidity": float(data["humidity"]),
        "humidity_s": str(data.get("humidity_s", data["humidity"])),
        "rain": float(data.get("rain", 0.0)),
        "rain_s": str(data.get("rain_s", data.get("rain", 0.0))),
        "fetched_at": time.time(),
    }


def station_sanity(st):
    """Plausibility checks on a station reading. Returns a list of problems."""
    problems = []
    if not (-30 <= st["temp"] <= 120):
        problems.append(f"temperature {st['temp']}F out of range")
    if not (20.0 <= st["pressure"] <= 35.0):
        problems.append(f"pressure {st['pressure']} inHg out of range")
    if not (0 <= st["humidity"] <= 100):
        problems.append(f"humidity {st['humidity']}% out of range")
    if not (0 <= st["wspd"] <= 120):
        problems.append(f"wind speed {st['wspd']} mph out of range")
    if not (0 <= st["rain"] <= 20):
        problems.append(f"rain total {st['rain']} in out of range")
    return problems


# ---------------------------------------------------------------- mapping

def map_condition(text):
    """Map free-form station condition text onto a Meso condition."""
    t = (text or "").strip().lower()
    if not t:
        return "Cloudy"
    for exact in MESO_CONDITIONS:
        if t == exact.lower():
            return exact
    if "thunder" in t or "storm" in t or "lightning" in t:
        return "Stormy"
    if "snow" in t or "sleet" in t or "hail" in t:
        return "Snowy"
    if "rain" in t or "shower" in t or "drizzle" in t:
        return "Rainy"
    if "fog" in t or "mist" in t or "haze" in t:
        return "Foggy"
    if "overcast" in t:
        return "Overcast"
    if "partly" in t:
        return "Partly Cloudy"
    if "mostly cloudy" in t or "cloudy" in t or "cloud" in t:
        return "Cloudy"
    if "clear" in t:
        return "Clear"
    if "sunny" in t or "fair" in t:
        return "Sunny"
    if "windy" in t:
        return "Windy"
    return "Cloudy"


def map_wind_direction(label, speed):
    """Map a station wind-direction label onto a Meso 16-point label.

    Returns None when calm (speed 0 or no usable direction); the API
    accepts a null windDirection, matching what the web form sends when
    the dropdown has no Calm option.
    """
    if speed == 0:
        return None
    if not label:
        return None
    lab = label.strip().upper()
    if lab in ("", "--", "CALM", "NONE"):
        return None
    if lab in COMPASS_16:
        return lab
    # Degrees to 16-point label.
    try:
        deg = float(re.sub(r"[^0-9.\-]", "", lab))
        return COMPASS_16[int((deg + 11.25) // 22.5) % 16]
    except ValueError:
        return None


def rainfall_duration_hours(now=None):
    """Rainfall-duration option closest to elapsed time since midnight local.

    Options the Meso form offers: 1, 5, 12, 24 hours.
    """
    now = now or datetime.now().astimezone()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = (now - midnight).total_seconds() / 3600.0
    return min(RAINFALL_DURATION_OPTIONS, key=lambda o: abs(o - elapsed))


def build_payload(cfg, st, now=None):
    """Build the POST /api/weather JSON body from config + station reading."""
    now = now or datetime.now(timezone.utc)
    payload = {
        "wxid": cfg["WXID"],
        "temperature": st["temp"],
        "humidity": st["humidity"],
        "windSpeed": st["wspd"],
        "windDirection": map_wind_direction(st.get("wdir", ""), st["wspd"]),
        "conditions": map_condition(st.get("cond", "")),
        "barometricPressure": st["pressure"],
        "rainfallAmount": st["rain"],
        "rainfallDurationHours": rainfall_duration_hours(),
        "notes": cfg.get("NOTES", ""),
        "latitude": float(cfg["LATITUDE"]),
        "longitude": float(cfg["LONGITUDE"]),
        "recordedAt": now.isoformat().replace("+00:00", "Z"),
    }
    return payload


# ---------------------------------------------------------------- Meso API

class MesoClient:
    """Minimal client for the Meso reporting API (cookie session)."""

    def __init__(self, base_url):
        self.base = base_url.rstrip("/")
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar))

    def _request(self, method, path, body=None):
        url = self.base + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"User-Agent": USER_AGENT}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=method)
        try:
            with self.opener.open(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(raw)
                msg = detail.get("error") or detail.get("message") or raw
            except ValueError:
                msg = raw or e.reason
            raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {msg}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"{method} {path} failed: {e.reason}")

    def unlock(self, wxid, password):
        """POST /api/wxids/{wxid}/unlock. Sets the session cookie."""
        status, _ = self._request(
            "POST", f"/api/wxids/{wxid}/unlock", {"password": password})
        if status not in (200, 201):
            raise RuntimeError(f"unlock returned HTTP {status}")
        return True

    def session_status(self, wxid):
        """GET /api/wxids/{wxid}/session. Returns the parsed JSON."""
        _, data = self._request("GET", f"/api/wxids/{wxid}/session")
        return data

    def submit(self, payload):
        """POST /api/weather. Returns the created record (parsed JSON)."""
        status, data = self._request("POST", "/api/weather", payload)
        if status not in (200, 201):
            raise RuntimeError(f"submit returned HTTP {status}")
        return data

    def records(self, wxid, limit=5):
        """GET /api/weather?wxid={wxid}. Public, no auth needed."""
        _, data = self._request("GET", f"/api/weather?wxid={wxid}")
        if isinstance(data, list):
            return data[:limit]
        return data
