"""Earth Engine + BigQuery spatio-temporal verification for Accent.AI.

Accent.AI: Audit of Carbon Credits and ENvironmental impacT on land using AI.
Layers inspected for the coordinates and sampling dates extracted from a filing:
  * CHIRPS daily precipitation (UCSB-CHG/CHIRPS/DAILY)  -> temporal confounding / dilution
  * ERA5-Land hourly 10 m wind (ECMWF/ERA5_LAND/HOURLY)   -> upwind sensor placement
  * SRTM 30 m DEM + slope (USGS/SRTMGL1_003)              -> control-plot comparability
  * Sentinel-2 SR NDVI (COPERNICUS/S2_SR_HARMONIZED)      -> seasonal canopy spin / land class
  * Hansen Global Forest Change (UMD/hansen/...)          -> buffer-ring leakage
  * BigQuery NOAA GSOD public dataset                     -> independent rain-gauge corroboration

Every live call is isolated in try/except. If Earth Engine is not authenticated
(or a single layer fails), a deterministic, seeded fallback generator produces
physically plausible telemetry so charts and maps always render.
"""
import datetime as dt
import hashlib
import json
import math
import os
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from schemas import (
    BufferEvidence,
    BufferRing,
    ExtractedFilingData,
    GeoPoint,
    LayerProvenance,
    NdviEvidence,
    RainfallEvidence,
    SatelliteEvidence,
    TerrainPoint,
    WindEvidence,
)
from services import force_offline, gcp_project, has_gcp_credentials, logger

EE_SCOPES = [
    "https://www.googleapis.com/auth/earthengine",
    "https://www.googleapis.com/auth/cloud-platform",
]
HANSEN_ASSETS = [
    "UMD/hansen/global_forest_change_2024_v1_12",
    "UMD/hansen/global_forest_change_2023_v1_11",
]
_EE_STATE: Dict[str, Any] = {"initialized": None, "detail": "not attempted"}


# ---------------------------------------------------------------------------
# Geodesy helpers (shared with demo_data and app)
# ---------------------------------------------------------------------------
EARTH_R_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_R_KM * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2 (0 = North, clockwise)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlmb = math.radians(lon2 - lon1)
    x = math.sin(dlmb) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlmb)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def destination_point(lat: float, lon: float, bearing: float, distance_km: float) -> Tuple[float, float]:
    d = distance_km / EARTH_R_KM
    b = math.radians(bearing)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2))
    return round(math.degrees(p2), 6), round((math.degrees(l2) + 540) % 360 - 180, 6)


def angular_diff(a: float, b: float) -> float:
    d = abs((a - b) % 360.0)
    return d if d <= 180 else 360 - d


def circle_polygon(lat: float, lon: float, radius_km: float, n: int = 72) -> List[List[float]]:
    """[lon, lat] ring for map layers."""
    return [list(destination_point(lat, lon, 360.0 * i / n, radius_km))[::-1] for i in range(n + 1)]


def wedge_polygon(lat: float, lon: float, bearing: float, half_angle: float, radius_km: float, n: int = 24) -> List[List[float]]:
    pts = [[lon, lat]]
    for i in range(n + 1):
        b = bearing - half_angle + (2 * half_angle) * i / n
        la, lo = destination_point(lat, lon, b, radius_km)
        pts.append([lo, la])
    pts.append([lon, lat])
    return pts


# ---------------------------------------------------------------------------
# Context resolution: which coordinates / dates matter for which check
# ---------------------------------------------------------------------------
_WATER_KW = ("water", "effluent", "river", "cr-vi", "chrom", "metal", "bod", "cod", "discharge", "outfall")
_AIR_KW = ("pm", "air", "so2", "no2", "ambient", "aqi", "stack")
_VEG_KW = ("ndvi", "canopy", "biomass", "forest", "tree", "carbon stock", "agb")


def _parse_date(value: str, default: Optional[dt.date] = None) -> dt.date:
    if value:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m"):
            try:
                return dt.datetime.strptime(value.strip()[:10], fmt).date()
            except ValueError:
                continue
    return default or (dt.date.today() - dt.timedelta(days=120))


def _first(points: List[GeoPoint], role: str) -> Optional[GeoPoint]:
    return next((p for p in points if p.role == role), None)


def _match(text: str, keywords: Tuple[str, ...]) -> bool:
    t = text.lower()
    return any(k in t for k in keywords)


def _context(extracted: ExtractedFilingData, profile: Dict[str, Any]) -> Dict[str, Any]:
    pts = extracted.coordinates
    project = _first(pts, "PROJECT_SITE") or (pts[0] if pts else GeoPoint(
        label="Unresolved Site", role="PROJECT_SITE", latitude=0.0, longitude=0.0, claimed_elevation_m=0.0))
    control = _first(pts, "CONTROL_SITE")
    sensors = [p for p in pts if p.role == "SENSOR_STATION"]
    air_sensor = next((s for s in sensors if _match(s.label, _AIR_KW)), None) or (sensors[0] if sensors else control)
    water_sensor = next((s for s in sensors if _match(s.label, _WATER_KW)), None)

    claims = extracted.sampling_claims
    water_claim = next((c for c in claims if _match(c.parameter, _WATER_KW)), None)
    air_claim = next((c for c in claims if _match(c.parameter, _AIR_KW)), None)
    veg_claim = next((c for c in claims if _match(c.parameter, _VEG_KW)), None)
    anchor = water_claim or veg_claim or (claims[0] if claims else None)

    rain_date = _parse_date(anchor.sampling_date if anchor else "")
    air_date = _parse_date(air_claim.sampling_date if air_claim else "", rain_date)
    meas_date = _parse_date(veg_claim.sampling_date if veg_claim else "", rain_date)
    ndvi_prof = profile.get("ndvi", {})
    default_baseline = dt.date(meas_date.year - 3, int(ndvi_prof.get("dry_month", 9)), 15)
    baseline_date = _parse_date(ndvi_prof.get("baseline_date", ""), default_baseline)
    is_carbon = extracted.filing_mode == "CARBON_OFFSET"
    radius = float(profile.get("buffer", {}).get("project_radius_km", 4.0 if is_carbon else 1.5))

    return dict(
        mode=extracted.filing_mode, is_carbon=is_carbon, project=project, control=control,
        air_sensor=air_sensor, water_sensor=water_sensor, points=pts,
        rain_date=rain_date, air_date=air_date, meas_date=meas_date, baseline_date=baseline_date,
        radius_km=radius, veg_claim=veg_claim,
    )


def _seed(*parts: Any) -> int:
    return int(hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:12], 16)


def _wet_peak_month(lat: float) -> int:
    if lat > 6:
        return 8      # SW / East-Asian summer monsoon
    if lat < -6:
        return 1      # Australian / southern monsoon
    return 12         # equatorial NE monsoon (Borneo, Sumatra)


def _dry_window(dates: List[str], mm: List[float]) -> str:
    """Pick the driest calendar month from the series and project its next occurrence."""
    totals: Dict[int, float] = {}
    for d, v in zip(dates, mm):
        m = int(d[5:7])
        totals[m] = totals.get(m, 0.0) + v
    driest = min(totals, key=totals.get) if totals else 2
    today = dt.date.today()
    year = today.year if driest > today.month else today.year + 1
    start = dt.date(year, driest, 1)
    end = (dt.date(year + (driest == 12), driest % 12 + 1, 1) - dt.timedelta(days=1))
    return f"{start.isoformat()} to {end.isoformat()} (climatological driest month, low-flow / no-rain antecedent)"


# ---------------------------------------------------------------------------
# Earth Engine initialisation
# ---------------------------------------------------------------------------
def init_earth_engine() -> Tuple[bool, str]:
    """Initialise Earth Engine once per process. Never raises."""
    if _EE_STATE["initialized"] is not None:
        return _EE_STATE["initialized"], _EE_STATE["detail"]
    ok, detail = False, ""
    if force_offline():
        ok, detail = False, "ACCENT_FORCE_OFFLINE=1"
    else:
        try:
            import ee

            key_file = os.getenv("EE_PRIVATE_KEY_FILE") or os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            if key_file and os.path.exists(key_file):
                sa = os.getenv("EE_SERVICE_ACCOUNT")
                if not sa:
                    with open(key_file) as fh:
                        sa = json.load(fh).get("client_email")
                credentials = ee.ServiceAccountCredentials(sa, key_file)
                how = f"service account {sa}"
            elif has_gcp_credentials():
                import google.auth

                credentials, _ = google.auth.default(scopes=EE_SCOPES)
                how = "Application Default Credentials"
            else:
                raise RuntimeError("no Earth Engine credentials mounted")
            project = os.getenv("EE_PROJECT") or gcp_project()
            ee.Initialize(credentials=credentials, project=project)
            ee.data.setDeadline(int(os.getenv("EE_DEADLINE_MS", "45000")))
            ee.Number(1).getInfo()  # smoke test
            ok, detail = True, f"Earth Engine live via {how} (project={project})"
        except Exception as exc:
            ok, detail = False, f"Earth Engine offline: {exc.__class__.__name__}: {str(exc)[:140]}"
    _EE_STATE.update(initialized=ok, detail=detail)
    logger.info(detail)
    return ok, detail


# ---------------------------------------------------------------------------
# LIVE layers (Earth Engine / BigQuery)
# ---------------------------------------------------------------------------
def _live_rainfall(ctx: Dict[str, Any]) -> RainfallEvidence:
    import ee

    site = ctx["water_sensor"] or ctx["project"]
    d: dt.date = ctx["rain_date"]
    pt = ee.Geometry.Point([site.longitude, site.latitude])
    chirps = ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY").select("precipitation")
    start, end = d - dt.timedelta(days=240), d + dt.timedelta(days=121)
    rows = chirps.filterDate(start.isoformat(), end.isoformat()).getRegion(pt, 5566).getInfo()
    header = rows[0]
    it, ip = header.index("time"), header.index("precipitation")
    series = sorted(
        (dt.datetime.utcfromtimestamp(r[it] / 1000).date(), float(r[ip] or 0.0)) for r in rows[1:]
    )
    if not series:
        raise RuntimeError("CHIRPS returned no rows")
    dates = [s[0].isoformat() for s in series]
    mm = [round(s[1], 2) for s in series]
    by_date = dict(series)
    v = by_date.get(d, max(by_date.items(), key=lambda kv: -abs((kv[0] - d).days))[1])
    antecedent = sum(by_date.get(d - dt.timedelta(days=k), 0.0) for k in range(0, 7))

    clim = chirps.filterDate("2009-01-01", "2025-01-01")
    stats = ee.Dictionary({
        "rank": clim.map(lambda i: i.lt(v).add(i.lte(v)).divide(2)).mean()
                    .reduceRegion(ee.Reducer.first(), pt, 5566).get("precipitation"),
        "p95": clim.reduce(ee.Reducer.percentile([95]))
                   .reduceRegion(ee.Reducer.first(), pt, 5566).get("precipitation_p95"),
        "p50wet": clim.map(lambda i: i.updateMask(i.gt(1))).reduce(ee.Reducer.median())
                      .reduceRegion(ee.Reducer.first(), pt, 5566).get("precipitation_median"),
    }).getInfo()
    pct = float(stats.get("rank") or 0.0) * 100
    dilution = float(np.clip(1.0 + antecedent / 18.0, 1.0, 15.0))
    return RainfallEvidence(
        dates=dates, precip_mm=mm, sampling_date=d.isoformat(), sampling_day_mm=round(v, 2),
        sampling_percentile=round(pct, 1), p50_wet_day_mm=round(float(stats.get("p50wet") or 0), 2),
        p95_mm=round(float(stats.get("p95") or 0), 2), antecedent_7d_mm=round(antecedent, 1),
        estimated_dilution_factor=round(dilution, 1), dry_window_suggestion=_dry_window(dates, mm),
    )


def _live_wind(ctx: Dict[str, Any]) -> WindEvidence:
    import ee

    src, sensor = ctx["project"], ctx["air_sensor"]
    d: dt.date = ctx["air_date"]
    pt = ee.Geometry.Point([src.longitude, src.latitude])
    era = (ee.ImageCollection("ECMWF/ERA5_LAND/HOURLY")
           .select(["u_component_of_wind_10m", "v_component_of_wind_10m"])
           .filterDate((d - dt.timedelta(days=15)).isoformat(), (d + dt.timedelta(days=15)).isoformat()))
    rows = era.getRegion(pt, 11132).getInfo()
    h = rows[0]
    iu, iv = h.index("u_component_of_wind_10m"), h.index("v_component_of_wind_10m")
    uv = np.array([[r[iu], r[iv]] for r in rows[1:] if r[iu] is not None and r[iv] is not None], dtype=float)
    if len(uv) == 0:
        raise RuntimeError("ERA5-Land returned no wind rows")
    return _wind_from_uv(ctx, uv[:, 0], uv[:, 1])


def _wind_from_uv(ctx: Dict[str, Any], u: np.ndarray, v: np.ndarray) -> WindEvidence:
    speed = np.hypot(u, v)
    from_deg = (np.degrees(np.arctan2(-u, -v)) + 360) % 360
    bins = np.arange(0, 360, 22.5)
    idx = np.round(from_deg / 22.5).astype(int) % 16
    freq = np.bincount(idx, minlength=16) / len(idx) * 100
    # speed-weighted vector mean of the "from" direction
    prevailing = (math.degrees(math.atan2(np.sum(-u), np.sum(-v))) + 360) % 360
    return _wind_evidence(ctx, list(bins), list(freq), prevailing, float(speed.mean()))


def _wind_evidence(ctx, bins, freq, prevailing, mean_speed, receptor_km: float = 1.4) -> WindEvidence:
    src, sensor = ctx["project"], ctx["air_sensor"]
    if sensor is None:
        sensor = src
    sb = bearing_deg(src.latitude, src.longitude, sensor.latitude, sensor.longitude)
    sd = haversine_km(src.latitude, src.longitude, sensor.latitude, sensor.longitude)
    offset = angular_diff(sb, prevailing)
    rlat, rlon = destination_point(src.latitude, src.longitude, (prevailing + 180) % 360, receptor_km)
    return WindEvidence(
        direction_bins_deg=[float(b) for b in bins], frequency_pct=[round(float(f), 2) for f in freq],
        prevailing_from_deg=round(prevailing, 1), mean_speed_ms=round(mean_speed, 2),
        sampling_date=ctx["air_date"].isoformat(), source_label=src.label, sensor_label=sensor.label,
        sensor_bearing_from_source_deg=round(sb, 1), sensor_distance_km=round(sd, 2),
        angular_offset_deg=round(offset, 1), sensor_is_upwind=bool(offset < 45 and sd > 0.2),
        downwind_receptor_lat=rlat, downwind_receptor_lon=rlon,
    )


def _live_terrain(ctx: Dict[str, Any]) -> List[TerrainPoint]:
    import ee

    dem = ee.Image("USGS/SRTMGL1_003")
    img = dem.rename("elev").addBands(ee.Terrain.slope(dem).rename("slope"))
    pts: List[GeoPoint] = ctx["points"]
    fc = ee.FeatureCollection([
        ee.Feature(ee.Geometry.Point([p.longitude, p.latitude]).buffer(90), {"i": i}) for i, p in enumerate(pts)
    ])
    res = img.reduceRegions(collection=fc, reducer=ee.Reducer.mean(), scale=30).getInfo()["features"]
    out = []
    for f in sorted(res, key=lambda f: f["properties"]["i"]):
        p = pts[f["properties"]["i"]]
        out.append(TerrainPoint(
            label=p.label, role=p.role, latitude=p.latitude, longitude=p.longitude,
            srtm_elevation_m=round(float(f["properties"].get("elev") or 0), 1),
            slope_deg=round(float(f["properties"].get("slope") or 0), 1),
            claimed_elevation_m=p.claimed_elevation_m,
        ))
    return out


def _s2_ndvi_collection(ee, geom):
    def to_ndvi(img):
        scl = img.select("SCL")
        clear = scl.neq(1).And(scl.neq(3)).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10))
        return img.updateMask(clear).normalizedDifference(["B8", "B4"]).rename("ndvi")

    return (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(geom)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 70)).map(to_ndvi))


def _live_ndvi(ctx: Dict[str, Any]) -> NdviEvidence:
    import ee

    p = ctx["project"]
    geom = ee.Geometry.Point([p.longitude, p.latitude]).buffer(250)
    s2 = _s2_ndvi_collection(ee, geom)
    start = dt.date(ctx["baseline_date"].year, ctx["baseline_date"].month, 1) - dt.timedelta(days=180)
    end = ctx["meas_date"] + dt.timedelta(days=95)
    months, cur = [], dt.date(start.year, start.month, 1)
    while cur <= end:
        nxt = dt.date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        months.append((cur, nxt))
        cur = nxt
    feats = []
    for ms, me in months:
        val = ee.Dictionary(
            s2.filterDate(ms.isoformat(), me.isoformat()).median()
            .reduceRegion(ee.Reducer.mean(), geom, 20, bestEffort=True)
        ).get("ndvi", -9)
        feats.append(ee.Feature(None, {"m": ms.isoformat(), "ndvi": val}))
    info = ee.FeatureCollection(feats).getInfo()["features"]
    series = [(f["properties"]["m"], float(f["properties"]["ndvi"])) for f in info
              if f["properties"].get("ndvi") is not None and float(f["properties"]["ndvi"]) > -1]
    if len(series) < 6:
        raise RuntimeError("insufficient cloud-free Sentinel-2 months")
    return _ndvi_evidence(ctx, [s[0] for s in series], [round(s[1], 3) for s in series])


def _ndvi_at(dates: List[str], values: List[float], target: dt.date) -> float:
    ds = [_parse_date(d) for d in dates]
    i = int(np.argmin([abs((x - target).days) for x in ds]))
    return float(values[i])


def _ndvi_evidence(ctx, dates, values, overrides: Optional[Dict[str, float]] = None) -> NdviEvidence:
    o = overrides or {}
    base_d, meas_d = ctx["baseline_date"], ctx["meas_date"]
    like_d = dt.date(base_d.year if meas_d.month >= base_d.month else base_d.year + 1, meas_d.month, 15)
    b = o.get("baseline_ndvi", _ndvi_at(dates, values, base_d))
    m = o.get("measurement_ndvi", _ndvi_at(dates, values, meas_d))
    ls = o.get("like_season_baseline_ndvi", _ndvi_at(dates, values, like_d))
    claimed = (m - b) / b * 100 if b else 0.0
    true = (m - ls) / ls * 100 if ls else 0.0
    artifact = float(np.clip((1 - true / claimed) * 100, 0, 100)) if claimed > 0.5 else 0.0
    current = o.get("project_site_current_ndvi", float(np.mean(values[-12:])))
    return NdviEvidence(
        dates=dates, ndvi=[round(float(v), 3) for v in values], baseline_date=base_d.isoformat(),
        baseline_ndvi=round(b, 3), measurement_date=meas_d.isoformat(), measurement_ndvi=round(m, 3),
        like_season_baseline_ndvi=round(ls, 3), claimed_gain_pct=round(claimed, 1),
        true_gain_pct=round(true, 1), seasonal_artifact_pct=round(artifact, 1),
        project_site_current_ndvi=round(current, 3),
    )


def _ring_specs(radius: float) -> List[Tuple[str, float, float]]:
    return [
        ("Inside project perimeter", 0.0, radius),
        ("Buffer 0–1 km", radius, radius + 1.0),
        ("Buffer 1–2.5 km", radius + 1.0, radius + 2.5),
        ("Buffer 2.5–5 km", radius + 2.5, radius + 5.0),
    ]


def _periods(ctx) -> Tuple[int, int, int, int]:
    start = ctx["baseline_date"].year + 1
    start = min(start, 2022)
    return start - 3, start - 1, start, min(start + 2, 2024)


def _live_buffer(ctx: Dict[str, Any]) -> BufferEvidence:
    import ee

    p, R = ctx["project"], ctx["radius_km"]
    center = ee.Geometry.Point([p.longitude, p.latitude])
    gfc = None
    for asset in HANSEN_ASSETS:
        try:
            gfc = ee.Image(asset)
            gfc.bandNames().getInfo()
            break
        except Exception:
            gfc = None
    if gfc is None:
        raise RuntimeError("Hansen GFC asset unavailable")
    a0, a1, b0, b1 = _periods(ctx)
    loss = gfc.select("lossyear")
    area = ee.Image.pixelArea().divide(1e4)
    recent = _s2_ndvi_collection(ee, center.buffer((R + 5) * 1000)).filterDate(
        (dt.date.today() - dt.timedelta(days=270)).isoformat(), dt.date.today().isoformat()).median()
    img = (loss.gte(a0 - 2000).And(loss.lte(a1 - 2000)).multiply(area).rename("pre")
           .addBands(loss.gte(b0 - 2000).And(loss.lte(b1 - 2000)).multiply(area).rename("post"))
           .addBands(recent.multiply(area).rename("ndvi_area"))
           .addBands(area.updateMask(recent.mask()).rename("area")))

    def ring_geom(inner, outer):
        g = center.buffer(outer * 1000, 25)
        return g if inner <= 0 else g.difference(center.buffer(inner * 1000, 25), 25)

    specs = _ring_specs(R)
    fc = ee.FeatureCollection([ee.Feature(ring_geom(i, o), {"k": k}) for k, (_, i, o) in enumerate(specs)])
    res = img.reduceRegions(collection=fc, reducer=ee.Reducer.sum(), scale=60, tileScale=4).getInfo()["features"]
    rings = []
    for f in sorted(res, key=lambda f: f["properties"]["k"]):
        label, inner, outer = specs[f["properties"]["k"]]
        pr = f["properties"]
        pre, post = float(pr.get("pre") or 0), float(pr.get("post") or 0)
        nd = float(pr.get("ndvi_area") or 0) / max(float(pr.get("area") or 1), 1e-6)
        rings.append(BufferRing(label=label, inner_km=inner, outer_km=outer, pre_period_loss_ha=round(pre, 1),
                                post_period_loss_ha=round(post, 1),
                                change_pct=round((post - pre) / pre * 100, 1) if pre > 0.5 else (100.0 if post > 1 else 0.0),
                                mean_ndvi=round(nd, 3)))
    peak = max(rings[1:], key=lambda r: r.change_pct)
    # Locate the leakage hotspot: 8 sectors of the peak ring
    sectors = []
    for s in range(8):
        poly = wedge_polygon(p.latitude, p.longitude, s * 45 + 22.5, 22.5, peak.outer_km + 0.2)
        sectors.append(ee.Feature(ee.Geometry.Polygon([poly]).intersection(ring_geom(peak.inner_km, peak.outer_km), 25), {"s": s}))
    sres = img.select("post").reduceRegions(collection=ee.FeatureCollection(sectors),
                                            reducer=ee.Reducer.sum(), scale=60, tileScale=4).getInfo()["features"]
    best = max(sres, key=lambda f: float(f["properties"].get("sum") or 0))["properties"]["s"]
    mid = (peak.inner_km + peak.outer_km) / 2
    hlat, hlon = destination_point(p.latitude, p.longitude, best * 45 + 22.5, mid)
    return BufferEvidence(
        project_radius_km=R, pre_period=f"{a0}–{a1}", post_period=f"{b0}–{b1}", rings=rings,
        inside_change_pct=rings[0].change_pct, peak_ring_label=peak.label, peak_change_pct=peak.change_pct,
        hotspot_lat=hlat, hotspot_lon=hlon, hotspot_distance_outside_km=round(mid - R, 2),
    )


def _bigquery_gsod_corroboration(lat: float, lon: float, d: dt.date) -> Optional[str]:
    """Cross-check CHIRPS against the nearest NOAA GSOD rain gauge (BigQuery public dataset)."""
    bq_enabled = os.getenv("ACCENT_ENABLE_BIGQUERY", os.getenv("TERRATRIAL_ENABLE_BIGQUERY", "1")) == "1"
    if not bq_enabled or not has_gcp_credentials():
        return None
    try:
        from google.cloud import bigquery

        client = bigquery.Client(project=gcp_project())
        sql = """
        WITH nearest AS (
          SELECT usaf, wban, name,
                 ST_DISTANCE(ST_GEOGPOINT(lon, lat), ST_GEOGPOINT(@lon, @lat)) / 1000 AS km
          FROM `bigquery-public-data.noaa_gsod.stations`
          WHERE lat BETWEEN @lat - 0.75 AND @lat + 0.75 AND lon BETWEEN @lon - 0.75 AND @lon + 0.75
          ORDER BY km LIMIT 1
        ),
        obs AS (
          SELECT DATE(CAST(g.year AS INT64), CAST(g.mo AS INT64), CAST(g.da AS INT64)) AS d, g.prcp
          FROM `bigquery-public-data.noaa_gsod.gsod*` g
          JOIN nearest n ON g.stn = n.usaf AND g.wban = n.wban
          WHERE _TABLE_SUFFIX BETWEEN '2010' AND @end_year AND g.prcp < 99
        ),
        ranked AS (SELECT d, prcp, PERCENT_RANK() OVER (ORDER BY prcp) AS pr FROM obs)
        SELECT (SELECT name FROM nearest) AS station, (SELECT km FROM nearest) AS km,
               prcp * 25.4 AS prcp_mm, pr * 100 AS pct
        FROM ranked WHERE d = @d
        """
        cfg = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("lat", "FLOAT64", lat),
            bigquery.ScalarQueryParameter("lon", "FLOAT64", lon),
            bigquery.ScalarQueryParameter("end_year", "STRING", str(d.year)),
            bigquery.ScalarQueryParameter("d", "DATE", d),
        ], maximum_bytes_billed=5 * 1024 ** 3)
        rows = list(client.query(sql, job_config=cfg).result(timeout=25))
        if not rows:
            return None
        r = rows[0]
        return (f"NOAA GSOD gauge '{r.station}' ({r.km:.0f} km): {r.prcp_mm:.1f} mm on {d.isoformat()} "
                f"→ {r.pct:.1f}th percentile of 2010–{d.year} record (BigQuery)")
    except Exception as exc:
        logger.info("BigQuery GSOD corroboration skipped: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Deterministic FALLBACK generators (seeded, physically plausible)
# ---------------------------------------------------------------------------
def _seasonal_weight(doy: np.ndarray, peak_month: int) -> np.ndarray:
    peak_doy = (peak_month - 0.5) * 30.44
    return ((1 + np.cos(2 * np.pi * (doy - peak_doy) / 365.25)) / 2) ** 2.2


def _synth_rain(rng, start: dt.date, n_days: int, peak_month: int, intensity: float) -> Tuple[List[dt.date], np.ndarray]:
    days = [start + dt.timedelta(days=i) for i in range(n_days)]
    doy = np.array([d.timetuple().tm_yday for d in days])
    w = _seasonal_weight(doy, peak_month)
    wet = rng.random(n_days) < (0.04 + 0.72 * w)
    amount = rng.gamma(0.85, (2.5 + 26 * w) * intensity)
    return days, np.where(wet, amount, 0.0)


def _fallback_rainfall(ctx, prof: Dict[str, Any], rng) -> RainfallEvidence:
    site = ctx["water_sensor"] or ctx["project"]
    d: dt.date = ctx["rain_date"]
    peak = int(prof.get("wet_peak_month", _wet_peak_month(site.latitude)))
    inten = float(prof.get("intensity", 1.0))
    clim_start = dt.date(d.year - 15, 1, 1)
    _, clim = _synth_rain(rng, clim_start, 15 * 365, peak, inten)
    start = d - dt.timedelta(days=240)
    days, mm = _synth_rain(rng, start, 361, peak, inten)
    si = 240
    if "target_percentile" in prof:
        target = float(prof["target_percentile"])
        mm[si] = float(np.percentile(clim, target))
        for k, q in zip((1, 2, 3), (96.5, 93.0, 90.0)):    # antecedent storm build-up
            mm[si - k] = max(mm[si - k], float(np.percentile(clim, q)))
    v = float(mm[si])
    pct = float((np.mean(clim < v) + np.mean(clim <= v)) / 2 * 100)
    antecedent = float(mm[si - 6: si + 1].sum())
    dilution = float(prof.get("dilution_factor", np.clip(1.0 + antecedent / 18.0, 1.0, 15.0)))
    dates = [x.isoformat() for x in days]
    vals = [round(float(x), 2) for x in mm]
    return RainfallEvidence(
        dates=dates, precip_mm=vals, sampling_date=d.isoformat(), sampling_day_mm=round(v, 1),
        sampling_percentile=round(pct, 1), p50_wet_day_mm=round(float(np.median(clim[clim > 1])), 1),
        p95_mm=round(float(np.percentile(clim, 95)), 1), antecedent_7d_mm=round(antecedent, 1),
        estimated_dilution_factor=round(dilution, 1), dry_window_suggestion=_dry_window(dates, vals),
    )


def _fallback_wind(ctx, prof: Dict[str, Any], rng) -> WindEvidence:
    src = ctx["project"]
    prevailing = float(prof.get("prevailing_from_deg", (rng.integers(0, 16) * 22.5)))
    spread = float(prof.get("spread_deg", 35.0))
    kappa = 1.0 / (math.radians(spread) ** 2)
    bins = np.arange(0, 360, 22.5)
    rad = np.radians(bins - prevailing)
    freq = np.exp(kappa * (np.cos(rad) - 1)) + 0.04 + 0.03 * rng.random(16)
    freq = freq / freq.sum() * 100
    return _wind_evidence(ctx, list(bins), list(freq), prevailing,
                          float(prof.get("mean_speed_ms", 2.5 + 2 * rng.random())),
                          float(prof.get("receptor_km", 1.4)))


def _fallback_terrain(ctx, prof: Dict[str, Any], rng) -> List[TerrainPoint]:
    out = []
    for p in ctx["points"]:
        o = prof.get(p.label) or prof.get(p.role) or {}
        base = p.claimed_elevation_m if (p.claimed_elevation_m is not None and p.claimed_elevation_m > 0) else 40 + 120 * rng.random()
        out.append(TerrainPoint(
            label=p.label, role=p.role, latitude=p.latitude, longitude=p.longitude,
            srtm_elevation_m=round(float(o.get("elevation_m", base + rng.normal(0, 6))), 1),
            slope_deg=round(float(o.get("slope_deg", 1.5 + 6 * rng.random())), 1),
            claimed_elevation_m=p.claimed_elevation_m, distance_to_road_km=o.get("road_km"),
        ))
    return out


def _fallback_ndvi(ctx, prof: Dict[str, Any], rng) -> NdviEvidence:
    p = ctx["project"]
    peak = int(prof.get("wet_peak_month", _wet_peak_month(p.latitude)))
    green_peak = (peak % 12) + 1                       # canopy greening lags rain ~1 month
    base_d, meas_d = ctx["baseline_date"], ctx["meas_date"]
    start = dt.date(base_d.year, base_d.month, 1) - dt.timedelta(days=180)
    end = meas_d + dt.timedelta(days=95)
    months, cur = [], dt.date(start.year, start.month, 15)
    while cur <= end:
        months.append(cur)
        cur = dt.date(cur.year + (cur.month == 12), cur.month % 12 + 1, 15)
    t0 = months[0]
    yrs = np.array([(m - t0).days / 365.25 for m in months])
    seas = np.array([math.cos(2 * math.pi * (m.month - green_peak) / 12) for m in months])

    def basis(d: dt.date):
        return [1.0, (d - t0).days / 365.25, math.cos(2 * math.pi * (d.month - green_peak) / 12)]

    level = 0.62 if ctx["is_carbon"] else 0.42
    coef = np.array([level + 0.1 * rng.random(), 0.004, 0.07 + 0.03 * rng.random()])
    anchors = {k: prof[k] for k in ("baseline_ndvi", "measurement_ndvi", "like_season_baseline_ndvi") if k in prof}
    like_d = dt.date(base_d.year if meas_d.month >= base_d.month else base_d.year + 1, meas_d.month, 15)
    if len(anchors) == 3:
        A = np.array([basis(base_d), basis(like_d), basis(meas_d)])
        y = np.array([anchors["baseline_ndvi"], anchors["like_season_baseline_ndvi"], anchors["measurement_ndvi"]])
        try:
            coef = np.linalg.solve(A, y)
        except np.linalg.LinAlgError:
            pass
    vals = coef[0] + coef[1] * yrs + coef[2] * seas + rng.normal(0, 0.009, len(months))
    vals = np.clip(vals, -0.1, 0.95)
    return _ndvi_evidence(ctx, [m.isoformat() for m in months], list(vals), overrides={
        **anchors, **({"project_site_current_ndvi": prof["project_site_current_ndvi"]}
                      if "project_site_current_ndvi" in prof else {})})


def _fallback_buffer(ctx, prof: Dict[str, Any], rng) -> BufferEvidence:
    p, R = ctx["project"], ctx["radius_km"]
    a0, a1, b0, b1 = _periods(ctx)
    specs = _ring_specs(R)
    changes = prof.get("ring_change_pct") or [round(float(rng.normal(-4, 6)), 1)] + \
        [round(float(rng.normal(3, 7)), 1) for _ in specs[1:]]
    ndvis = prof.get("ring_ndvi") or [round(0.45 + 0.25 * rng.random(), 3) for _ in specs]
    rate = float(prof.get("baseline_loss_rate_pct", 0.6 if ctx["is_carbon"] else 0.25)) / 100
    rings = []
    for (label, inner, outer), ch, nd in zip(specs, changes, ndvis):
        area_ha = math.pi * (outer ** 2 - inner ** 2) * 100
        pre = area_ha * rate * 3 * (0.8 + 0.4 * rng.random())
        post = pre * (1 + ch / 100)
        rings.append(BufferRing(label=label, inner_km=inner, outer_km=outer, pre_period_loss_ha=round(pre, 1),
                                post_period_loss_ha=round(post, 1), change_pct=round(float(ch), 1),
                                mean_ndvi=round(float(nd), 3)))
    peak = max(rings[1:], key=lambda r: r.change_pct)
    offset = float(prof.get("hotspot_offset_km", (peak.inner_km + peak.outer_km) / 2 - R))
    brg = float(prof.get("hotspot_bearing_deg", rng.integers(0, 8) * 45 + 22.5))
    hlat, hlon = destination_point(p.latitude, p.longitude, brg, R + offset)
    return BufferEvidence(
        project_radius_km=R, pre_period=f"{a0}–{a1}", post_period=f"{b0}–{b1}", rings=rings,
        inside_change_pct=rings[0].change_pct, peak_ring_label=peak.label, peak_change_pct=peak.change_pct,
        hotspot_lat=hlat, hotspot_lon=hlon, hotspot_distance_outside_km=round(offset, 2),
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
_DATASETS = {
    "Precipitation": "CHIRPS Daily v2.0 (UCSB-CHG/CHIRPS/DAILY)",
    "Wind vectors": "ERA5-Land Hourly 10 m u/v (ECMWF/ERA5_LAND/HOURLY)",
    "Elevation & slope": "SRTM 1-arcsec DEM (USGS/SRTMGL1_003)",
    "Vegetation NDVI": "Sentinel-2 L2A SR (COPERNICUS/S2_SR_HARMONIZED)",
    "Buffer-ring loss": "Hansen Global Forest Change + Sentinel-2 NDVI",
}


def verify_filing(extracted: ExtractedFilingData, profile: Optional[Dict[str, Any]] = None,
                  prefer_live: bool = True) -> SatelliteEvidence:
    """Run every spatio-temporal check layer. Never raises."""
    profile = profile or {}
    ctx = _context(extracted, profile)
    p = ctx["project"]
    rng = np.random.default_rng(_seed(p.latitude, p.longitude, ctx["rain_date"], extracted.filing_mode))
    live = prefer_live and init_earth_engine()[0]
    fallback_mode = "BENCHMARK_REPLAY" if profile else "DETERMINISTIC_FALLBACK"
    fb_detail = ("Curated benchmark telemetry (golden case replay)" if profile
                 else "Earth Engine credentials not mounted — seeded deterministic telemetry")
    from services.gemini_extractor import is_filing_pre_2015
    datasets = dict(_DATASETS)
    if is_filing_pre_2015(extracted):
        datasets["Vegetation NDVI"] = "Landsat 5/7 SR (LANDSAT/LT05 + LE07)"
        datasets["Buffer-ring loss"] = "Hansen Global Forest Change + Landsat 5/7 NDVI"
    provenance: List[LayerProvenance] = []

    def run(layer: str, live_fn, fb_fn):
        ds_name = datasets[layer]
        if live:
            try:
                result = live_fn(ctx)
                provenance.append(LayerProvenance(layer=layer, dataset=ds_name, mode="LIVE",
                                                  detail="Queried live from Earth Engine"))
                return result
            except Exception as exc:
                logger.warning("EE layer %s failed, using fallback: %s", layer, exc)
                detail = f"Live query failed ({exc.__class__.__name__}); fallback telemetry"
                provenance.append(LayerProvenance(layer=layer, dataset=ds_name, mode=fallback_mode, detail=detail))
                return fb_fn()
        provenance.append(LayerProvenance(layer=layer, dataset=ds_name, mode=fallback_mode, detail=fb_detail))
        return fb_fn()

    rainfall = run("Precipitation", _live_rainfall, lambda: _fallback_rainfall(ctx, profile.get("rain", {}), rng))
    wind = None
    if not ctx["is_carbon"] and ctx["air_sensor"] is not None:
        wind = run("Wind vectors", _live_wind, lambda: _fallback_wind(ctx, profile.get("wind", {}), rng))
    terrain = run("Elevation & slope", _live_terrain, lambda: _fallback_terrain(ctx, profile.get("terrain", {}), rng))
    ndvi = run("Vegetation NDVI", _live_ndvi, lambda: _fallback_ndvi(ctx, profile.get("ndvi", {}), rng))
    buffer = run("Buffer-ring loss", _live_buffer, lambda: _fallback_buffer(ctx, profile.get("buffer", {}), rng))

    if prefer_live:
        site = ctx["water_sensor"] or p
        rainfall.bigquery_corroboration = _bigquery_gsod_corroboration(site.latitude, site.longitude, ctx["rain_date"])
    if rainfall.bigquery_corroboration is None and profile.get("rain", {}).get("gsod_note"):
        rainfall.bigquery_corroboration = profile["rain"]["gsod_note"]

    # Recommended ground-truth coordinate
    if ctx["is_carbon"]:
        if buffer.peak_change_pct >= 10:
            lat, lon = buffer.hotspot_lat, buffer.hotspot_lon
            why = f"Leakage hotspot in '{buffer.peak_ring_label}' ({buffer.peak_change_pct:+.0f}% canopy loss), " \
                  f"{buffer.hotspot_distance_outside_km:.1f} km outside the project perimeter"
        else:
            lat, lon = p.latitude, p.longitude
            why = "Project centroid — routine canopy verification plot"
    else:
        if wind is not None and wind.sensor_is_upwind:
            lat, lon = wind.downwind_receptor_lat, wind.downwind_receptor_lon
            why = f"Downwind plume receptor {1.4:.1f} km from {wind.source_label} along the prevailing " \
                  f"{(wind.prevailing_from_deg + 180) % 360:.0f}° transport vector"
        elif ctx["water_sensor"] is not None:
            lat, lon = ctx["water_sensor"].latitude, ctx["water_sensor"].longitude
            why = f"Effluent outfall / mixing-zone station '{ctx['water_sensor'].label}'"
        else:
            lat, lon = p.latitude, p.longitude
            why = "Project site boundary"

    return SatelliteEvidence(
        filing_mode=extracted.filing_mode, provenance=provenance, rainfall=rainfall, ndvi=ndvi, wind=wind,
        terrain=terrain, buffer=buffer, recommended_dispatch_lat=lat, recommended_dispatch_lon=lon,
        recommended_dispatch_rationale=why,
    )


def fallback_evidence(extracted: ExtractedFilingData) -> SatelliteEvidence:
    """Generate minimal deterministic fallback evidence without live Earth Engine queries."""
    return verify_filing(extracted, profile=None, prefer_live=False)
