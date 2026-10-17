"""Pre-loaded golden benchmark cases for Accent.AI live demos.

Accent.AI: Audit of Carbon Credits and ENvironmental impacT on land using AI.
All proponents, projects and identifiers are FICTIONAL benchmark scenarios placed at
realistic JAPAC geographies. Each case bundles:
  * `extracted`  — what Gemini would extract from the filing (ExtractedFilingData)
  * `profile`    — curated satellite/reanalysis telemetry replayed by the verifier
  * `sample_pdf` — a generated PDF of the filing, for exercising the live uploader
"""
from typing import Any, Dict, List

from schemas import ExtractedFilingData, GeoPoint, SamplingClaim
from services.earth_engine_verifier import destination_point

# ---------------------------------------------------------------------------
# CASE 1 — EIA: riverfront chrome-tanning expansion (Ganga floodplain, Uttar Pradesh)
# ---------------------------------------------------------------------------
_STACK = (26.4300, 80.4120)
_AQ1 = destination_point(*_STACK, 295.0, 2.1)          # 2.1 km UPWIND (prevailing WNW wind)
_W2 = destination_point(*_STACK, 135.0, 0.45)          # outfall mixing zone on the river
_VILLAGE = destination_point(*_STACK, 115.0, 1.6)      # downwind settlement

EIA_CASE = ExtractedFilingData(
    filing_mode="EIA_CLEARANCE",
    subsector="INDUSTRIAL_EIA",
    forestry_metric_type="NOT_APPLICABLE",
    is_satellite_verifiable=True,
    unsupported_reason=None,
    project_title="Riverfront Chrome-Tanning & Effluent Treatment Expansion (Phase II)",
    applicant_or_proponent="Gangetic Hide & Chrome Processors Pvt. Ltd. (fictional)",
    region_name="Jajmau industrial belt, Kanpur Nagar, Uttar Pradesh, India",
    governing_body="CPCB / UPPCB",
    governing_statute="Environment Protection Act / Water Act Standards",
    summary_of_claim=("Proponent seeks Environmental Clearance for a 2.4x capacity expansion, asserting that river "
                      "Cr-VI at the outfall (0.04 mg/L) and ambient PM2.5 (38 ug/m3) are well within statutory limits "
                      "and that the 1.5 km riparian strip is degraded scrubland of low ecological value."),
    coordinates=[
        GeoPoint(label="Main Chrome-Tanning Boiler Stack S-1", role="PROJECT_SITE",
                 latitude=_STACK[0], longitude=_STACK[1], claimed_elevation_m=126.0),
        GeoPoint(label="Baseline Ambient Air Sensor AQ-1", role="SENSOR_STATION",
                 latitude=_AQ1[0], longitude=_AQ1[1], claimed_elevation_m=127.0),
        GeoPoint(label="Effluent Water Sampling Point W-2 (Outfall)", role="SENSOR_STATION",
                 latitude=_W2[0], longitude=_W2[1], claimed_elevation_m=118.0),
        GeoPoint(label="Downwind Riverside Settlement", role="BUFFER_ZONE",
                 latitude=_VILLAGE[0], longitude=_VILLAGE[1], claimed_elevation_m=0.0),
    ],
    sampling_claims=[
        SamplingClaim(parameter="River Heavy Metal (Cr-VI) at Outfall W-2", claimed_project_value=0.04,
                      claimed_control_or_limit_value=0.10, unit="mg/L", sampling_date="2025-08-14",
                      page_citation="Page 42, Table 3.4", is_concentration_pollutant=True,
                      governing_body="CPCB", governing_statute="Environment Protection Act"),
        SamplingClaim(parameter="BOD (3-day, 27 C) at Outfall W-2", claimed_project_value=18.0,
                      claimed_control_or_limit_value=30.0, unit="mg/L", sampling_date="2025-08-14",
                      page_citation="Page 43, Table 3.5", is_concentration_pollutant=True,
                      governing_body="CPCB", governing_statute="Environment Protection Act"),
        SamplingClaim(parameter="Ambient PM2.5 (24-h) at AQ-1", claimed_project_value=38.0,
                      claimed_control_or_limit_value=60.0, unit="ug/m3", sampling_date="2025-11-21",
                      page_citation="Page 57, Table 4.2", is_concentration_pollutant=True,
                      governing_body="CPCB / NAAQS", governing_statute="Air (Prevention and Control of Pollution) Act"),
    ],
    extracted_methodology_notes=[
        "“Water quality sampling at W-2 was conducted on 14 August 2025 under normal operating and representative "
        "hydrological conditions.” (Page 41, Section 3.3)",
        "“Ambient air monitoring station AQ-1 was located at the north-western boundary of the cluster to ensure "
        "uninterrupted power supply and site security.” (Page 55, Section 4.1)",
        "“Predominant wind direction during the study period was variable to calm.” — no wind rose is provided. "
        "(Page 56)",
        "“The 1.5 km riparian strip adjoining the site is classified as degraded riverine scrubland of low "
        "ecological value.” (Page 61, Section 5.2)",
    ],
)

EIA_PROFILE: Dict[str, Any] = {
    "rain": {
        "target_percentile": 98.2, "dilution_factor": 8.0, "wet_peak_month": 8, "intensity": 1.15,
        "gsod_note": "Nearest NOAA GSOD rain gauge (~9 km, BigQuery benchmark replay): 96.5 mm on 2025-08-14 "
                     "→ 98.6th percentile of the 2010–2025 record",
    },
    "wind": {"prevailing_from_deg": 295.0, "spread_deg": 30.0, "mean_speed_ms": 3.1, "receptor_km": 1.4},
    "terrain": {
        "PROJECT_SITE": {"elevation_m": 126.4, "slope_deg": 0.8},
        "SENSOR_STATION": {"elevation_m": 127.9, "slope_deg": 1.1},
        "BUFFER_ZONE": {"elevation_m": 123.6, "slope_deg": 0.6},
    },
    "ndvi": {"wet_peak_month": 8, "dry_month": 4, "project_site_current_ndvi": 0.47},
    "buffer": {
        "project_radius_km": 1.5, "ring_change_pct": [3.0, 6.5, 4.0, 2.0],
        "ring_ndvi": [0.47, 0.58, 0.52, 0.44], "baseline_loss_rate_pct": 0.2,
    },
}

# ---------------------------------------------------------------------------
# CASE 2 — Carbon Offset: tropical REDD+ / afforestation (East Kalimantan, Indonesia)
# ---------------------------------------------------------------------------
_CENTER = (0.5620, 116.8840)
_CONTROL = destination_point(*_CENTER, 52.0, 7.5)       # rocky ridge beside logging highway
_COMMUNITY = destination_point(*_CENTER, 250.0, 5.5)

CARBON_CASE = ExtractedFilingData(
    filing_mode="CARBON_OFFSET",
    subsector="FORESTRY_REDD",
    forestry_metric_type="AVOIDED_DEFORESTATION_RATE",
    is_satellite_verifiable=True,
    unsupported_reason=None,
    project_title="Mahakam Lowland Dipterocarp REDD+ & Afforestation Project (VCS-ID 9921, fictional)",
    applicant_or_proponent="PT Khatulistiwa Canopy Carbon (fictional)",
    region_name="Kutai Kartanegara Regency, East Kalimantan, Indonesia",
    governing_body="Verra / VCS",
    governing_statute="Verified Carbon Standard (VM0007)",
    summary_of_claim=("Project Design Document claims 412,000 tCO2e of avoided emissions and canopy restoration over "
                      "5,030 ha, citing a +22.7% NDVI gain versus baseline, a 3.9%/yr counterfactual deforestation "
                      "rate from a comparable reference plot, and zero leakage in the 5 km leakage belt."),
    coordinates=[
        GeoPoint(label="Project Area Centroid (Stratum A)", role="PROJECT_SITE",
                 latitude=_CENTER[0], longitude=_CENTER[1], claimed_elevation_m=85.0),
        GeoPoint(label="Reference Control Plot RC-1", role="CONTROL_SITE",
                 latitude=_CONTROL[0], longitude=_CONTROL[1], claimed_elevation_m=95.0),
        GeoPoint(label="Community Forest Boundary (Leakage Belt West)", role="BUFFER_ZONE",
                 latitude=_COMMUNITY[0], longitude=_COMMUNITY[1], claimed_elevation_m=0.0),
    ],
    sampling_claims=[
        SamplingClaim(parameter="Canopy NDVI (Sentinel-2, Stratum A)", claimed_project_value=0.81,
                       claimed_control_or_limit_value=0.66, unit="NDVI", sampling_date="2025-12-18",
                       page_citation="Page 31, Table 5.2", is_concentration_pollutant=False,
                       governing_body="Verra", governing_statute="VCS Standard VM0007"),
        SamplingClaim(parameter="Baseline Deforestation Rate (Reference Control Plot RC-1)",
                       claimed_project_value=0.2, claimed_control_or_limit_value=3.9, unit="%/yr",
                       sampling_date="2025-06-30", page_citation="Page 24, Table 4.1", is_concentration_pollutant=False,
                       governing_body="Verra", governing_statute="VCS Standard VM0007"),
        SamplingClaim(parameter="Above-Ground Biomass Stock (Stratum A)", claimed_project_value=214.0,
                       claimed_control_or_limit_value=131.0, unit="tCO2e/ha", sampling_date="2025-12-18",
                       page_citation="Page 33, Table 5.4", is_concentration_pollutant=False,
                       governing_body="Verra", governing_statute="VCS Standard VM0007"),
        SamplingClaim(parameter="Leakage Belt Forest Loss (0-5 km)", claimed_project_value=0.0,
                       claimed_control_or_limit_value=10.0, unit="%", sampling_date="2025-12-18",
                       page_citation="Page 38, Section 6.3", is_concentration_pollutant=False,
                       governing_body="Verra", governing_statute="VCS Standard VM0007"),
    ],
    extracted_methodology_notes=[
        "“Reference control plot RC-1 was selected for its comparable lowland dipterocarp composition and similar "
        "topography to the project area.” (Page 23, Section 4.1)",
        "“Baseline canopy NDVI was established from imagery acquired in September 2022; monitoring-period NDVI from "
        "imagery acquired 18 December 2025.” (Page 30, Section 5.1)",
        "“No activity-shifting leakage has been observed in the 5 km leakage belt; leakage deduction is set to "
        "0%.” (Page 38, Section 6.3)",
        "“Access to RC-1 is facilitated by the adjacent provincial timber haul road.” (Page 24, footnote 7)",
    ],
)

CARBON_PROFILE: Dict[str, Any] = {
    "rain": {"target_percentile": 93.5, "wet_peak_month": 12, "intensity": 1.0},
    "terrain": {
        "PROJECT_SITE": {"elevation_m": 88.0, "slope_deg": 5.6},
        "CONTROL_SITE": {"elevation_m": 412.0, "slope_deg": 34.2, "road_km": 0.12},
        "BUFFER_ZONE": {"elevation_m": 102.0, "slope_deg": 7.9},
    },
    "ndvi": {
        "wet_peak_month": 12, "dry_month": 9, "baseline_date": "2022-09-15",
        "baseline_ndvi": 0.66, "like_season_baseline_ndvi": 0.79, "measurement_ndvi": 0.81,
    },
    "buffer": {
        "project_radius_km": 4.0, "ring_change_pct": [-6.0, 9.0, 42.0, 13.0],
        "ring_ndvi": [0.78, 0.71, 0.58, 0.66], "baseline_loss_rate_pct": 0.9,
        "hotspot_bearing_deg": 60.0, "hotspot_offset_km": 1.8,
    },
}

GOLDEN_CASES: Dict[str, Dict[str, Any]] = {
    "eia": {
        "key": "eia", "title": "EIA · Riverfront Chrome Tannery", "emoji": "🏭",
        "tagline": "Monsoon-diluted Cr-VI · upwind air sensor", "extracted": EIA_CASE, "profile": EIA_PROFILE,
        "filename": "EIA_Riverfront_Chrome_Tannery_PhaseII.pdf",
    },
    "carbon": {
        "key": "carbon", "title": "Carbon · Kalimantan REDD+", "emoji": "🌳",
        "tagline": "Post-rain NDVI · 34° control plot · buffer leakage", "extracted": CARBON_CASE,
        "profile": CARBON_PROFILE, "filename": "PDD_Mahakam_REDD_Afforestation.pdf",
    },
}


# ---------------------------------------------------------------------------
# Minimal dependency-free PDF writer (sample filings for the live uploader)
# ---------------------------------------------------------------------------
def _wrap(text: str, width: int = 100) -> List[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def _ascii(s: str) -> str:
    table = {"“": '"', "”": '"', "—": "-", "–": "-", "→": "->", "°": " deg", "’": "'", "µ": "u"}
    for k, v in table.items():
        s = s.replace(k, v)
    return s.encode("ascii", "ignore").decode()


def _filing_lines(case: Dict[str, Any]) -> List[str]:
    x: ExtractedFilingData = case["extracted"]
    is_carbon = x.filing_mode == "CARBON_OFFSET"
    doc = "PROJECT DESIGN DOCUMENT (PDD) - VCS REDD+ / ARR" if is_carbon else "ENVIRONMENTAL IMPACT ASSESSMENT REPORT"
    lines = [doc, "", f"Project Title: {x.project_title}", f"Proponent: {x.applicant_or_proponent}",
             f"Region: {x.region_name}", ""]
    lines += _wrap(f"Executive Summary: {x.summary_of_claim}")
    lines += ["", "SECTION 2 - SITE GEOMETRY (decimal degrees, WGS84)"]
    for p in x.coordinates:
        ns, ew = ("N" if p.latitude >= 0 else "S"), ("E" if p.longitude >= 0 else "W")
        elev = f", elevation {p.claimed_elevation_m:.0f} m" if p.claimed_elevation_m else ""
        lines.append(f"{p.label}: {abs(p.latitude):.4f} {ns}, {abs(p.longitude):.4f} {ew}{elev}")
    lines += ["", "SECTION 3 - BASELINE MONITORING RESULTS",
              "Parameter | Reported Value | Limit / Control | Sampling Date | Citation"]
    for c in x.sampling_claims:
        ref = "baseline" if is_carbon else "limit"
        lines.append(f"{c.parameter} | {c.claimed_project_value:g} {c.unit} | {ref} {c.claimed_control_or_limit_value:g} "
                     f"{c.unit} | sampled {c.sampling_date} | {c.page_citation}")
    lines += ["", "SECTION 4 - METHODOLOGY NOTES"]
    for n in x.extracted_methodology_notes:
        lines += _wrap(n.split("” (")[0].strip("“”") + ".")
    lines += ["", "Conclusion: The proponent certifies that all baselines are representative and compliant."]
    return [_ascii(l) for l in lines]


def build_sample_pdf(case_key: str) -> bytes:
    """Render a golden case as a simple text PDF (Helvetica, US-Letter)."""
    lines = _filing_lines(GOLDEN_CASES[case_key])
    per_page = 52
    pages = [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[""]]
    objs: List[bytes] = []
    n_pages = len(pages)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n_pages))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    for i, page in enumerate(pages):
        ops = ["BT", "/F1 9 Tf", "54 750 Td"]
        for j, line in enumerate(page):
            esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            if j:
                ops.append("0 -13.5 Td")
            ops.append(f"({esc}) Tj")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
                    f"/Contents {5 + 2 * i} 0 R >>".encode())
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for k, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{k} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
