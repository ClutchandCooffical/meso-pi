#!/usr/bin/env python3
"""Storm Prediction Center Day 1 outlook reader for meso-pi.

Ported from wx-brief-pi's spc.py (same author, same logic). The SPC Day 1
text product does NOT list counties by risk level. It gives the risk areas
as polygons of latitude/longitude coordinates (the "areal outline"
product, WUUS01 PTSDY1). This module fetches that product and tests the
station point against each polygon exactly, and measures how close the
point sits to a contour edge so an edge-uncertain reading can be flagged
instead of guessed.

Coordinate decoding (SPC areal outline format): each point is an 8-digit
token. First four digits are latitude x100 (2473 = 24.73N). Last four are
longitude x100 with the hundreds digit dropped when west of 100W, so
8223 = 82.23W and 0046 = 100.46W. The token 99999999 separates polygons.

Standard library only.
"""
import math
import re
import time
from datetime import datetime, timedelta, timezone

import mesocommon as mc

SPC_INDEX = "https://www.spc.noaa.gov/products/outlook/day1otlk.html"
SPC_ARCHIVE = "https://www.spc.noaa.gov/products/outlook/archive/{year}/KWNSPTSDY1_{stamp}.txt"

CAT_ORDER = {"TSTM": 0, "MRGL": 1, "SLGT": 2, "ENH": 3, "MDT": 4, "HIGH": 5}
ISSUANCE_TIMES = ["2000", "1630", "1300", "0600", "0100"]


def _decode_token(tok):
    lat = int(tok[:4]) / 100.0
    lonv = int(tok[4:])
    lon = lonv / 100.0 if lonv >= 6600 else 100.0 + lonv / 100.0
    return (lat, -lon)


def _split_polygons(tokens):
    polys, cur = [], []
    for tok in tokens:
        if tok == "99999999":
            if len(cur) >= 3:
                polys.append(cur)
            cur = []
            continue
        cur.append(_decode_token(tok))
    if len(cur) >= 3:
        polys.append(cur)
    return polys


def _slice(text, start_marker, end_markers):
    """Text between a start marker and the nearest of the end markers."""
    i = text.find(start_marker)
    if i < 0:
        return ""
    seg = text[i + len(start_marker):]
    end = len(seg)
    for em in end_markers:
        j = seg.find(em)
        if 0 <= j < end:
            end = j
    return seg[:end]


def _parse_keys(seg, keys):
    """Parse key lines (e.g. 'MRGL   24738223 ...') within one slice.

    A key can appear on several lines (one polygon per line group); each
    occurrence plus its continuation lines becomes its own polygons.
    """
    out = {}
    for key in keys:
        polys = []
        for m in re.finditer(r"(?m)^" + re.escape(key) + r"[ \t]+((?:\d{8}[ \t]*)+)",
                             seg):
            body = m.group(1)
            # Continuation lines: lines made only of 8-digit tokens.
            for line in seg[m.end():].splitlines():
                if line.strip() == "":
                    continue
                if re.fullmatch(r"[ \t]*(?:\d{8}[ \t]*)+", line or ""):
                    body += " " + line
                else:
                    break
            tokens = re.findall(r"\d{8}", body)
            polys.extend(_split_polygons(tokens))
        if polys:
            out[key] = polys
    return out


def parse_spc(text):
    """Parse the areal outline text into structured polygons."""
    ac = re.search(r"SPC AC\s+(\d+)", text)
    valid = re.search(r"VALID TIME\s+(\d+Z\s*-\s*\d+Z)", text)
    prob = _slice(text, "PROBABILISTIC OUTLOOK POINTS DAY 1",
                  ["CATEGORICAL OUTLOOK POINTS DAY 1"])
    tor_seg = _slice(prob, "... TORNADO ...", ["... HAIL ..."])
    hail_seg = _slice(prob, "... HAIL ...", ["... WIND ..."])
    wind_seg = _slice(prob, "... WIND ...", ["&&"])
    cat_seg = _slice(text, "... CATEGORICAL ...", ["&&"])
    out = {
        "ac": ac.group(1) if ac else None,
        "valid": valid.group(1).replace(" ", "") if valid else None,
        "tornado": _parse_keys(tor_seg, ["0.02", "0.05", "0.10", "0.15",
                                         "0.30", "0.45", "0.60"]),
        "hail": _parse_keys(hail_seg, ["0.05", "0.15", "0.30", "0.45", "0.60"]),
        "wind": _parse_keys(wind_seg, ["0.05", "0.15", "0.30", "0.45", "0.60"]),
        "categorical": _parse_keys(cat_seg, ["TSTM", "MRGL", "SLGT", "ENH",
                                             "MDT", "HIGH"]),
    }
    return out


def _point_in_polygon(lat, lon, poly):
    """Ray casting. poly is a list of (lat, lon)."""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        yi, xi = poly[i]
        yj, xj = poly[j]
        if (yi > lat) != (yj > lat):
            xinters = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < xinters:
                inside = not inside
        j = i
    return inside


def _point_segment_deg(lat, lon, a, b):
    """Distance in degrees from a point to a line segment (flat approx)."""
    (y1, x1), (y2, x2) = a, b
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(lat - y1, lon - x1)
    t = max(0.0, min(1.0, ((lat - y1) * dy + (lon - x1) * dx) / (dx * dx + dy * dy)))
    return math.hypot(lat - (y1 + t * dy), lon - (x1 + t * dx))


def _edge_distance_deg(lat, lon, polys):
    """Shortest distance in degrees from the point to any polygon edge."""
    best = None
    for poly in polys:
        for i in range(len(poly)):
            d = _point_segment_deg(lat, lon, poly[i], poly[(i + 1) % len(poly)])
            if best is None or d < best:
                best = d
    return best


def evaluate(parsed, lat, lon):
    """Test the station point against every SPC layer.

    Returns {cat, cat_edge_deg, tornado, tornado_edge_deg, wind, hail,
    wind_edge_deg, hail_edge_deg}. Probabilities are the highest contour
    containing the point, or None. Edge distances are in degrees (0.25 deg
    is roughly 15 miles); a point within that of a contour is edge-uncertain.
    """
    res = {"cat": None, "cat_edge_deg": None, "tornado": None,
           "tornado_edge_deg": None, "wind": None, "wind_edge_deg": None,
           "hail": None, "hail_edge_deg": None}

    def best(layer, key_order=None):
        found, edge = None, None
        for key, polys in layer.items():
            inside = any(_point_in_polygon(lat, lon, p) for p in polys)
            d = _edge_distance_deg(lat, lon, polys)
            if d is not None and (edge is None or d < edge):
                edge = d
            if inside:
                if found is None or (key_order and key_order[key] > key_order[found]):
                    found = key
        return found, edge

    cat, cedge = best(parsed["categorical"], CAT_ORDER)
    res["cat"], res["cat_edge_deg"] = cat, cedge
    tor, tedge = best(parsed["tornado"])
    if tor is not None:
        inside_keys = [k for k, polys in parsed["tornado"].items()
                       if any(_point_in_polygon(lat, lon, p) for p in polys)]
        tor = max(inside_keys, key=float) if inside_keys else None
    res["tornado"], res["tornado_edge_deg"] = tor, tedge
    for name in ("wind", "hail"):
        found, edge = best(parsed[name])
        if found is not None:
            inside_keys = [k for k, polys in parsed[name].items()
                           if any(_point_in_polygon(lat, lon, p) for p in polys)]
            found = max(inside_keys, key=float) if inside_keys else None
        res[name], res[name + "_edge_deg"] = found, edge
    return res


def fetch_spc():
    """Fetch the current Day 1 areal outline text.

    First follows the PTSDY1 archive link on the outlook index page; if
    that fails, tries the known issuance times for today and yesterday.
    Returns (text, source_url, ac_number_or_None). The areal outline text
    itself carries no AC number, so the AC comes from the index page when
    available, else from the archive filename stamp (DDHHMM).
    Raises RuntimeError if nothing works.
    """
    try:
        index = mc.fetch_text(SPC_INDEX, attempts=2)
        m = re.search(r'href="(archive/\d{4}/KWNSPTSDY1_\d+\.txt)"', index)
        acm = re.search(r"SPC AC\s+(\d+)", index)
        ac = acm.group(1) if acm else None
        if m:
            url = "https://www.spc.noaa.gov/products/outlook/" + m.group(1)
            text = mc.fetch_text(url, attempts=2)
            if "DAY 1 CONVECTIVE OUTLOOK AREAL OUTLINE" in text:
                if ac is None:
                    sm = re.search(r"KWNSPTSDY1_(\d{8})(\d{4})\.txt", url)
                    ac = sm.group(2) and (sm.group(1)[6:] + sm.group(2))
                return text, url, ac
    except Exception:
        pass
    now = datetime.now(timezone.utc)
    for day_off in (0, 1):
        day = now - timedelta(days=day_off)
        for hhmm in ISSUANCE_TIMES:
            stamp = day.strftime("%Y%m%d") + hhmm
            url = SPC_ARCHIVE.format(year=day.strftime("%Y"), stamp=stamp)
            try:
                text = mc.fetch_text(url, attempts=1, pause=0)
            except Exception:
                continue
            if "DAY 1 CONVECTIVE OUTLOOK AREAL OUTLINE" in text:
                return text, url, day.strftime("%d") + hhmm
    raise RuntimeError("could not fetch a current Day 1 areal outline")


def spc_status(cfg):
    """Full SPC read for the station. Returns a dict, never raises.

    On failure returns {"ok": False, "error": ...} so the caller can
    decide instead of crashing.
    """
    try:
        text, url, ac = fetch_spc()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    parsed = parse_spc(text)
    if not parsed["categorical"]:
        return {"ok": False, "error": "areal outline parsed empty"}
    try:
        lat = float(cfg["LATITUDE"])
        lon = float(cfg["LONGITUDE"])
    except (ValueError, TypeError):
        return {"ok": False, "error": "LATITUDE/LONGITUDE are not numbers"}
    ev = evaluate(parsed, lat, lon)
    ev.update({"ok": True, "ac": ac or parsed["ac"], "valid": parsed["valid"],
               "source": url})
    return ev


if __name__ == "__main__":
    cfg = mc.load_config()
    st = spc_status(cfg)
    print(st)
