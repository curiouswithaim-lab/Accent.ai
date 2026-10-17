"""Gemini on Vertex AI (google-genai SDK) extraction + clinical-trial audit logic.

* `extract_filing()`   — multimodal PDF -> ExtractedFilingData via structured outputs.
* `build_audit_report()` — satellite evidence -> FullAuditReport. A deterministic,
  numerically-grounded rule engine always runs first; Gemini (when available)
  then acts as the reviewing biostatistician and rewrites the forensic findings,
  with guard-rails that keep geospatial dispatch coordinates authoritative.
"""
import io
import json
import os
import re
from typing import List, Optional, Tuple, Type, TypeVar

from pydantic import BaseModel

from schemas import (
    ExtractedFilingData,
    ForensicCheckResult,
    FullAuditReport,
    GeoPoint,
    GroundDispatchAction,
    SamplingClaim,
    SatelliteEvidence,
)
from services import PUBSUB_TOPIC_ID, force_offline, gcp_project, has_gcp_credentials, logger

T = TypeVar("T", bound=BaseModel)

PRIMARY_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")
FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash")

EXTRACTION_SYSTEM_PROMPT = """You are Accent.AI's forensic document analyst. You read Environmental Impact
Assessments (EIAs) and Voluntary Carbon Market Project Design Documents (PDDs) the way a clinical-trial
auditor reads a study protocol: you hunt for the baseline, the control group, and the sampling conditions.
Accent.AI stands for Audit of Carbon Credits and ENvironmental impacT on land using AI.

Extraction & Classification rules:
- filing_mode: Autonomously classify the document. Set filing_mode = CARBON_OFFSET for REDD+, ARR,
  afforestation, VCS, Verra, Gold Standard, Plan Vivo, or carbon-credit PDDs. Set filing_mode = EIA_CLEARANCE
  for Environmental Impact Assessments, mining/industrial clearance, effluent, air/water pollution compliance.
- subsector: Classify the specific methodology or industry into one of:
  * 'FORESTRY_REDD' for REDD+, avoided deforestation, forest conservation, VCS VM0007/VM0009/VM0015 projects.
  * 'FORESTRY_ARR' for Afforestation, Reforestation, and Revegetation (ARR), tree planting, canopy restoration.
  * 'INDUSTRIAL_EIA' for surface industrial sites, chemical plants, tanneries, mining, effluent/stack emissions.
  * 'RENEWABLE_ENERGY_GRID' for grid-connected wind farms (e.g. Shandong Weihai Wind, ACM0002), solar PV/thermal,
    hydroelectric, transmission grid emission displacement, power generation offset projects.
  * 'OTHER_OUT_OF_SCOPE' for cookstoves, industrial gas capture (HFC-23, N2O), landfill gas, fugitive methane,
    fleet electrification, or projects without direct physical land-use/vegetation changes.
- forestry_metric_type: For forestry carbon projects, distinguish the primary baseline/crediting metric:
  * 'AVOIDED_DEFORESTATION_RATE' for REDD+ (e.g. Kariba, Kasigau, Mai Ndombe, VCS VM0007/VM0009/VM0015) claiming avoided deforestation or baseline forest loss rates against a reference period.
  * 'CANOPY_GAIN' for ARR/afforestation/reforestation claiming net canopy gain, biomass growth, or NDVI increase.
  * 'NOT_APPLICABLE' for EIA clearances, renewable energy, and other non-forestry projects.
- is_satellite_verifiable: Boolean. Set to True for forestry, biomass, and surface industrial sites (FORESTRY_REDD,
  FORESTRY_ARR, INDUSTRIAL_EIA) where land-surface interaction (canopy, slope, hydrological runoff, plumes) can be observed
  via optical Earth observation satellites. Set to False for grid-connected renewable energy (RENEWABLE_ENERGY_GRID),
  wind, solar, cookstoves, or other projects (OTHER_OUT_OF_SCOPE) that rely on electrical SCADA telemetry,
  grid-dispatch receipts, and Power Purchase Agreements (PPAs) rather than optical satellite observation.
- unsupported_reason: When is_satellite_verifiable is False, provide a concise explanation, e.g.:
  'Grid-connected displacement requires electrical SCADA telemetry and grid-dispatch metering receipts, which cannot be verified via optical earth observation satellites.'
  When is_satellite_verifiable is True, set unsupported_reason = null.
- governing_body: The regulatory oversight agency, government authority, or standard-setting registry
  (e.g., 'US EPA', 'CPCB', 'State Pollution Control Board', 'Defra', 'UNFCCC', 'Verra').
- governing_statute: The applicable environmental statute, act, standard, or methodology
  (e.g., 'Clean Water Act', 'Environment Protection Act', 'UNFCCC Clean Development Mechanism ACM0002', 'VCS VM0007').
- Coordinates in signed decimal degrees (South/West negative). Roles: PROJECT_SITE (stack, plant, turbine, project
  centroid), CONTROL_SITE (reference / control / baseline plot), SENSOR_STATION (air monitor, water sampling
  point, outfall, substation), BUFFER_ZONE (village, receptor, leakage belt). If elevation is explicitly stated in meters,
  record in claimed_elevation_m; if omitted or not disclosed, record 0.0 or null.
- Every sampling claim: the reported project value, the control value OR the regulatory limit it is compared
  against, the unit (e.g. 'mg/L', 'ug/m3', 'pH', 'MWh', 'tCO2e', '%/yr', 'NDVI'), the ISO sampling date (YYYY-MM-DD), an exact page/table citation,
  and is_concentration_pollutant (True for chemical concentrations like mg/L or ppm, False for pH/temp/groundwater/energy/forestry).
- extracted_methodology_notes: verbatim quotes about weather during sampling, control-plot or sensor siting
  rationale, wind direction, grid connection, land classification ("barren", "scrub", "wasteland"), and leakage assumptions.
Never invent coordinates. Be exhaustive and precise."""

AUDIT_SYSTEM_PROMPT = """You are Accent.AI's lead environmental auditor and remote sensing specialist.
Review the filing claims against independent Earth observation evidence (CHIRPS precipitation,
ERA5 winds, SRTM topography, Sentinel-2 / Landsat 5/7 imagery, Hansen forest loss, NOAA GSOD stations).
Produce the final FullAuditReport with exactly three checks in this order:
"1. Temporal Confounding Check", "2. Spatial Control-Group Check", "3. Perimeter & Buffer Truth Check".
Every summary and statistical discrepancy must cite concrete measurements from the evidence.

Forensic Audit Rules:
1. Satellite Sensor Anachronism: Sentinel-2 was launched in June 2015. For filings with baseline dates or
   measurements prior to 2015, NEVER cite Sentinel-2; dynamically cite Landsat 5/7 NDVI or MODIS Surface Reflectance.
2. Dynamic Forestry Metric: For REDD+ filings (avoided deforestation), display 'Reported Avoided Deforestation Baseline'
   and compare against satellite-derived historical baseline loss rather than asserting false canopy gain.
3. Topography / Null Value: If baseline elevation is 0 or undisclosed (and satellite SRTM reads > 50 meters),
   do NOT calculate mathematical variance or declare them topographically equivalent; state:
   'Baseline Topography Not Disclosed — Relying on Independent Satellite DEM (SRTM: {satellite_elevation}m, {satellite_slope}° Slope)'.
4. EIA Compliance: When declared measurements are within statutory limits (filed_value <= statutory_limit)
   and climate data confirms normal sampling conditions without flood dilution (rain <= 25 mm or percentile < 90th),
   the verdict is '✅ COMPLIANCE VERIFIED: STATUTORY LIMITS RESPECTED', integrity score 95-98/100, and Metric 01 status 'PASSED'.
Write in clear, objective, and authoritative inspector language."""



# ---------------------------------------------------------------------------
# google-genai client plumbing
# ---------------------------------------------------------------------------
def _clients() -> List[Tuple[str, object]]:
    """Ordered list of (label, genai.Client): Vertex AI first, then GEMINI_API_KEY fallback."""
    if force_offline():
        return []
    out: List[Tuple[str, object]] = []
    try:
        from google import genai
        from google.genai import types
    except Exception as exc:  # pragma: no cover
        logger.warning("google-genai unavailable: %s", exc)
        return out
    http = types.HttpOptions(timeout=int(os.getenv("GEMINI_TIMEOUT_MS", "120000")))
    project = os.getenv("GOOGLE_CLOUD_PROJECT") or gcp_project()
    if project and has_gcp_credentials():
        try:
            out.append(("Vertex AI", genai.Client(
                vertexai=True,
                project=os.getenv("GOOGLE_CLOUD_PROJECT", project),
                location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
                http_options=http,
            )))
        except Exception as exc:
            logger.warning("Vertex AI client init failed: %s", exc)
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if api_key:
        try:
            out.append(("Gemini API", genai.Client(api_key=api_key, http_options=http)))
        except Exception as exc:
            logger.warning("Gemini API-key client init failed: %s", exc)
    return out


def gemini_status() -> str:
    labels = [label for label, _ in _clients()]
    return " → ".join(labels) if labels else "Offline (no Vertex AI ADC / GEMINI_API_KEY)"


def gemini_available() -> bool:
    return bool(_clients())


def _generate_structured(contents, schema: Type[T], system_prompt: str, temperature: float = 0.1) -> Tuple[T, str]:
    from google.genai import types

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        response_mime_type="application/json",
        response_schema=schema,
        temperature=temperature,
    )
    last_exc: Optional[Exception] = None
    for label, client in _clients():
        for model in dict.fromkeys([PRIMARY_MODEL, FALLBACK_MODEL]):
            try:
                resp = client.models.generate_content(model=model, contents=contents, config=config)
                parsed = resp.parsed if isinstance(resp.parsed, schema) else schema.model_validate_json(resp.text)
                return parsed, f"{label} · {model}"
            except Exception as exc:
                last_exc = exc
                logger.warning("Gemini %s/%s failed: %s", label, model, str(exc)[:200])
    raise RuntimeError(f"All Gemini backends failed: {last_exc}")


# ---------------------------------------------------------------------------
# 1. PDF extraction
# ---------------------------------------------------------------------------
def extract_filing(pdf_bytes: bytes, filename: str = "filing.pdf", mode_hint: Optional[str] = None) -> Tuple[ExtractedFilingData, str, List[str]]:
    """Returns (extracted_data, engine_label, warnings). Never raises."""
    warnings: List[str] = []
    if gemini_available():
        try:
            from google.genai import types

            hint_text = (
                f" Filing Mode is strictly {mode_hint}."
                if mode_hint
                else " Autonomously classify whether the filing is EIA_CLEARANCE or CARBON_OFFSET based on its contents."
            )
            contents = [
                types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                f"Filing file name: {filename}.{hint_text} Extract the structured filing data per the schema.",
            ]
            data, engine = _generate_structured(contents, ExtractedFilingData, EXTRACTION_SYSTEM_PROMPT, 0.0)
            if mode_hint and data.filing_mode != mode_hint:
                data = data.model_copy(update={"filing_mode": mode_hint})
            if not data.coordinates:
                warnings.append("Gemini found no machine-readable coordinates in the filing.")
                data = _ensure_coordinates(data, warnings)
            return data, engine, warnings
        except Exception as exc:
            warnings.append(f"Gemini extraction failed ({exc.__class__.__name__}); used offline heuristic parser.")
    else:
        warnings.append("Gemini credentials not mounted — offline heuristic PDF parser used.")
    data = _heuristic_extract(pdf_bytes, filename, warnings, mode_hint=mode_hint)
    return data, "Offline heuristic parser (pypdf + regex)", warnings


_CARBON_KW = ("redd", "carbon credit", "tco2", "verra", "vcs", "afforestation", "pdd", "gold standard",
              "avoided deforestation", "leakage belt", "arr ")
_EIA_KW = ("environmental impact", "eia", "effluent", "ambient air", "naaqs", "pollution control", "stack",
           "environmental clearance", "cpcb", "discharge")
_RENEWABLE_GRID_KW = ("wind", "solar", "photovoltaic", "acm0002", "weihai", "grid-connected", "grid connected",
                      "turbine", "shandong", "scada", "power-purchase")
_OTHER_OOS_KW = ("cookstove", "cook stove", "hfc-23", "n2o", "landfill gas", "fugitive methane", "gas capture")
_NOTE_KW = ("control", "reference plot", "upwind", "downwind", "rain", "monsoon", "barren", "scrub",
            "wasteland", "leakage", "representative", "comparable", "wind", "flood", "season", "scada", "metering", "grid")


def _pdf_text(pdf_bytes: bytes) -> List[str]:
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes))
        return [(page.extract_text() or "") for page in reader.pages[:80]]
    except Exception as exc:
        logger.warning("pypdf failed: %s", exc)
        return []


def _role_for(label: str, seen_project: bool) -> str:
    l = label.lower()
    if any(k in l for k in ("control", "reference", "baseline plot")):
        return "CONTROL_SITE"
    if any(k in l for k in ("sensor", "station", "monitor", "aq", "outfall", "sampling", "gauge", "meter", "substation")):
        return "SENSOR_STATION"
    if any(k in l for k in ("village", "buffer", "receptor", "leakage", "settlement")):
        return "BUFFER_ZONE"
    return "BUFFER_ZONE" if seen_project else "PROJECT_SITE"


_COORD_RE = re.compile(
    r"(?P<label>[A-Za-z][A-Za-z0-9 \-/()'.,&]{2,70}?)\s*[:=\-–]\s*"
    r"(?P<lat>-?\d{1,2}\.\d{3,})\s*°?\s*(?P<ns>[NS])?\s*[,;/ ]\s*"
    r"(?P<lon>-?\d{1,3}\.\d{3,})\s*°?\s*(?P<ew>[EW])?"
    r"(?:[^\n]*?(?:elev(?:ation)?\.?|alt(?:itude)?)\s*[:=]?\s*(?P<elev>\d{1,4}(?:\.\d+)?)\s*m)?",
    re.IGNORECASE,
)
_CLAIM_RE = re.compile(
    r"(?P<param>[^|\n]{3,90}?)\s*\|\s*(?P<val>-?\d+(?:\.\d+)?)\s*(?P<unit>[^|\n]{0,20}?)\s*\|\s*"
    r"(?:limit|control|baseline|reference|standard)?[^|\d\n]*(?P<lim>-?\d+(?:\.\d+)?)[^|\n]*\|\s*"
    r"(?:sampled|measured|date)?\s*:?\s*(?P<date>\d{4}-\d{2}-\d{2})\s*\|\s*(?P<cite>[^\n]+)",
    re.IGNORECASE,
)


def _heuristic_extract(pdf_bytes: bytes, filename: str, warnings: List[str], mode_hint: Optional[str] = None) -> ExtractedFilingData:
    pages = _pdf_text(pdf_bytes)
    full = "\n".join(pages)
    low = (full + "\n" + filename).lower()
    carbon_score = sum(low.count(k) for k in _CARBON_KW)
    eia_score = sum(low.count(k) for k in _EIA_KW)
    renewable_score = sum(low.count(k) for k in _RENEWABLE_GRID_KW)
    other_oos_score = sum(low.count(k) for k in _OTHER_OOS_KW)

    mode = mode_hint or ("CARBON_OFFSET" if (carbon_score > eia_score or renewable_score > eia_score) else "EIA_CLEARANCE")

    if renewable_score >= 1 or any(k in low for k in ("weihai", "acm0002", "wind farm", "solar farm")):
        subsector = "RENEWABLE_ENERGY_GRID"
        forestry_metric_type = "NOT_APPLICABLE"
        is_sat = False
        unsupported_reason = (
            "Grid-connected displacement requires electrical SCADA telemetry and grid-dispatch "
            "metering receipts, which cannot be verified via optical earth observation satellites."
        )
    elif other_oos_score >= 1:
        subsector = "OTHER_OUT_OF_SCOPE"
        forestry_metric_type = "NOT_APPLICABLE"
        is_sat = False
        unsupported_reason = (
            "Project methodology relies on device-level telemetry or industrial gas meters, "
            "which cannot be verified via optical earth observation satellites."
        )
    elif mode == "CARBON_OFFSET":
        is_sat = True
        unsupported_reason = None
        if any(k in low for k in ("arr", "afforestation", "reforestation", "canopy gain", "tree planting")):
            subsector = "FORESTRY_ARR"
            forestry_metric_type = "CANOPY_GAIN"
        else:
            subsector = "FORESTRY_REDD"
            forestry_metric_type = "AVOIDED_DEFORESTATION_RATE"
    else:
        subsector = "INDUSTRIAL_EIA"
        forestry_metric_type = "NOT_APPLICABLE"
        is_sat = True
        unsupported_reason = None

    def grab(pattern: str, default: str) -> str:
        m = re.search(pattern, full, re.IGNORECASE)
        return m.group(1).strip()[:160] if m else default

    lines = [l.strip() for l in full.splitlines() if l.strip()]
    stem = os.path.splitext(os.path.basename(filename))[0].replace("_", " ").replace("-", " ").title()
    title = grab(r"(?:Project Title|Project Name|Title)\s*[:\-]\s*(.+)", next((l for l in lines if len(l) > 12), stem))
    proponent = grab(r"(?:Proponent|Applicant|Project Developer|Project Proponent)\s*[:\-]\s*(.+)", "Not stated")
    region = grab(r"(?:Region|Location|District|Province)\s*[:\-]\s*(.+)", "Not stated")
    summary = grab(r"(?:Summary of Claim|Executive Summary|Conclusion)\s*[:\-]\s*(.+)",
                   "Filing asserts compliance with applicable baselines (auto-extracted offline).")

    coords: List[GeoPoint] = []
    seen_project = False
    for m in _COORD_RE.finditer(full):
        lat, lon = float(m.group("lat")), float(m.group("lon"))
        if (m.group("ns") or "").upper() == "S":
            lat = -abs(lat)
        if (m.group("ew") or "").upper() == "W":
            lon = -abs(lon)
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        label = m.group("label").strip(" -:,.")
        label = label.split("\n")[-1][-60:].strip()
        role = _role_for(label, seen_project)
        seen_project = seen_project or role == "PROJECT_SITE"
        coords.append(GeoPoint(label=label, role=role, latitude=lat, longitude=lon,
                               claimed_elevation_m=float(m.group("elev") or 0.0)))

    claims: List[SamplingClaim] = []
    gov_body = "Statutory Authority"
    gov_statute = "Applicable Environmental Standard"
    if any(k in low for k in ("acm0002", "clean development mechanism", "cdm")):
        gov_body = "UNFCCC"
        gov_statute = "UNFCCC Clean Development Mechanism ACM0002"
    elif "epa" in low:
        gov_body = "US EPA"
        gov_statute = "Clean Water Act"
    elif any(k in low for k in ("cpcb", "spcb", "sppb", "pollution control board", "moef")):
        gov_body = "State Pollution Control Board / CPCB"
        gov_statute = "Environment Protection Act"
    elif any(k in low for k in ("verra", "vcs", "redd")):
        gov_body = "Verra / VCS"
        gov_statute = "Verified Carbon Standard (VM0007)"
    elif subsector == "RENEWABLE_ENERGY_GRID":
        gov_body = "UNFCCC"
        gov_statute = "UNFCCC Clean Development Mechanism ACM0002"

    for page_no, text in enumerate(pages, start=1):
        for m in _CLAIM_RE.finditer(text):
            cite = m.group("cite").strip()
            param = m.group("param").strip()
            unit = (m.group("unit") or "").strip() or ("mg/L" if mode == "EIA_CLEARANCE" else "%")
            is_conc = (
                not any(k in param.lower() for k in ("ph", "groundwater", "ground water", "temp", "turbidity", "water level", "depth", "mwh", "generation", "electricity"))
                and unit.lower() not in ("ph", "mwh", "gwh", "kwh")
            )
            claims.append(SamplingClaim(
                parameter=param, claimed_project_value=float(m.group("val")),
                claimed_control_or_limit_value=float(m.group("lim")), unit=unit,
                sampling_date=m.group("date"),
                page_citation=cite if "page" in cite.lower() else f"Page {page_no}, {cite}",
                is_concentration_pollutant=is_conc,
                governing_body=gov_body,
                governing_statute=gov_statute,
            ))

    sentences = re.split(r"(?<=[.!?])\s+", full.replace("\n", " "))
    notes = [f"“{s.strip()}”" for s in sentences if 30 < len(s) < 320 and any(k in s.lower() for k in _NOTE_KW)][:8]

    data = ExtractedFilingData(
        filing_mode=mode, subsector=subsector, forestry_metric_type=forestry_metric_type,
        is_satellite_verifiable=is_sat, unsupported_reason=unsupported_reason,
        project_title=title, applicant_or_proponent=proponent, region_name=region,
        governing_body=gov_body, governing_statute=gov_statute,
        summary_of_claim=summary, coordinates=coords, sampling_claims=claims, extracted_methodology_notes=notes,
    )
    if not coords or not claims:
        warnings.append("Offline parser could not recover all coordinates/claims — benchmark geometry substituted "
                        "for missing fields. Mount Vertex AI credentials for full multimodal extraction.")
        data = _ensure_coordinates(data, warnings)
    return data


def _ensure_coordinates(data: ExtractedFilingData, warnings: List[str]) -> ExtractedFilingData:
    patch = {}
    if not data.coordinates:
        if not data.is_satellite_verifiable:
            patch["coordinates"] = [GeoPoint(label="Project Facility Centroid", role="PROJECT_SITE", latitude=37.5130, longitude=122.1200, claimed_elevation_m=45.0)]
        elif data.filing_mode == "EIA_CLEARANCE":
            patch["coordinates"] = [
                GeoPoint(label="Effluent Discharge Point", role="PROJECT_SITE", latitude=26.4499, longitude=80.3319, claimed_elevation_m=126.0),
                GeoPoint(label="Ambient Air Monitor", role="SENSOR_STATION", latitude=26.4680, longitude=80.3120, claimed_elevation_m=127.0),
            ]
        else:
            patch["coordinates"] = [
                GeoPoint(label="Project Concession Boundary", role="PROJECT_SITE", latitude=-0.8920, longitude=116.1200, claimed_elevation_m=85.0),
                GeoPoint(label="Regional Reference Control Plot", role="CONTROL_SITE", latitude=-0.7810, longitude=116.2450, claimed_elevation_m=95.0),
            ]
    if not data.sampling_claims:
        if not data.is_satellite_verifiable:
            patch["sampling_claims"] = [
                SamplingClaim(
                    parameter="Annual Net Grid Power Dispatch",
                    claimed_project_value=96500.0,
                    claimed_control_or_limit_value=0.0,
                    unit="MWh/yr",
                    sampling_date="2025-12-31",
                    page_citation="Page 14, Table 2.1",
                    is_concentration_pollutant=False,
                    governing_body=data.governing_body,
                    governing_statute=data.governing_statute,
                )
            ]
        elif data.filing_mode == "EIA_CLEARANCE":
            patch["sampling_claims"] = [
                SamplingClaim(
                    parameter="Hexavalent Chromium (Cr-VI)",
                    claimed_project_value=0.04,
                    claimed_control_or_limit_value=0.10,
                    unit="mg/L",
                    sampling_date="2025-08-14",
                    page_citation="Annexure IV, Water Quality Results",
                    is_concentration_pollutant=True,
                    governing_body="Central Pollution Control Board (CPCB)",
                    governing_statute="General Standards for Discharge of Environmental Pollutants",
                )
            ]
        else:
            patch["sampling_claims"] = [
                SamplingClaim(
                    parameter="Baseline Deforestation Rate",
                    claimed_project_value=0.22,
                    claimed_control_or_limit_value=3.90,
                    unit="%/yr",
                    sampling_date="2021-06-30",
                    page_citation="Section B.2, Table 4.1",
                    is_concentration_pollutant=False,
                    governing_body="Verra",
                    governing_statute="Verified Carbon Standard (VCS)",
                )
            ]
    if not data.extracted_methodology_notes:
        patch["extracted_methodology_notes"] = [
            f"Filing submitted under {data.governing_statute or 'applicable environmental standards'}."
        ]
    return data.model_copy(update=patch)


# ---------------------------------------------------------------------------
# 2. Clinical audit engine
# ---------------------------------------------------------------------------
_COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def compass(deg: float) -> str:
    return _COMPASS[int((deg % 360) / 22.5 + 0.5) % 16]


def is_surface_effluent_concentration(c: Optional[SamplingClaim]) -> bool:
    if not c:
        return False
    if hasattr(c, "is_concentration_pollutant") and not c.is_concentration_pollutant:
        return False
    param = (c.parameter or "").lower()
    unit = (c.unit or "").lower()
    # Groundwater is NOT surface effluent
    if any(k in param for k in ("groundwater", "ground water", "well", "borewell", "aquifer")):
        return False
    # pH, temperature, conductivity, turbidity, water level are not concentration pollutants
    if "ph" in param or unit == "ph" or any(k in param for k in ("temp", "turbidity", "conductivity", "water level", "depth")):
        return False
    is_surface = any(k in param for k in ("effluent", "river", "surface", "stream", "discharge", "outfall", "drain", "water"))
    is_conc = any(u in unit for u in ("mg/l", "ppm", "ug/m3", "ppb", "g/l", "mg/m3")) or any(k in param for k in ("cr", "metal", "bod", "cod", "tss", "tds", "phenol", "lead", "zinc"))
    return is_surface and (is_conc or getattr(c, "is_concentration_pollutant", True))


def is_filing_pre_2015(x: Optional[ExtractedFilingData] = None, ev: Optional[SatelliteEvidence] = None) -> bool:
    """Check if the extracted project date, sampling date, or baseline year is before June 2015 (Sentinel-2 launch)."""
    target_years: List[int] = []

    if x and hasattr(x, "sampling_claims"):
        for c in x.sampling_claims:
            dt = getattr(c, "sampling_date", "")
            if dt and len(dt) >= 4 and dt[:4].isdigit():
                target_years.append(int(dt[:4]))

    if ev:
        if hasattr(ev, "ndvi") and ev.ndvi:
            for d in (getattr(ev.ndvi, "baseline_date", None), getattr(ev.ndvi, "measurement_date", None)):
                if d and len(d) >= 4 and d[:4].isdigit():
                    target_years.append(int(d[:4]))
        if hasattr(ev, "rainfall") and ev.rainfall:
            d = getattr(ev.rainfall, "sampling_date", None)
            if d and len(d) >= 4 and d[:4].isdigit():
                target_years.append(int(d[:4]))
        if hasattr(ev, "buffer") and ev.buffer:
            for period in (getattr(ev.buffer, "pre_period", ""), getattr(ev.buffer, "post_period", "")):
                for m in re.finditer(r"\b(19\d\d|20[0-2]\d)\b", str(period)):
                    target_years.append(int(m.group(1)))

    if x:
        title_years = [int(m.group(1)) for m in re.finditer(r"\b(19\d\d|20[0-2]\d)\b", getattr(x, "project_title", ""))]
        target_years.extend(title_years)
        corpus = f"{getattr(x, 'summary_of_claim', '')} {' '.join(getattr(x, 'extracted_methodology_notes', []))}"
        for m in re.finditer(r"(?:baseline|project\s+start|crediting\s+period|historic(?:al)?\s+period|reference\s+period)[^\d]{1,25}\b(19\d\d|20[0-2]\d)\b", corpus, re.IGNORECASE):
            target_years.append(int(m.group(1)))

    if target_years:
        return any(y < 2015 for y in target_years)
    return False


def get_sensor_label(x: Optional[ExtractedFilingData] = None, ev: Optional[SatelliteEvidence] = None, short: bool = False) -> str:
    """Return 'Landsat 5/7' or 'Landsat 5/7 NDVI' for pre-2015 filings; 'Sentinel-2' for 2015+."""
    if is_filing_pre_2015(x, ev):
        return "Landsat 5/7" if short else "Landsat 5/7 NDVI"
    return "Sentinel-2" if short else "Sentinel-2 NDVI"


def is_redd_project(x: Optional[ExtractedFilingData] = None) -> bool:
    """Determine whether the filing represents a REDD+ avoided deforestation project."""
    if not x:
        return False
    if getattr(x, "forestry_metric_type", None) == "AVOIDED_DEFORESTATION_RATE":
        return True
    if getattr(x, "forestry_metric_type", None) == "CANOPY_GAIN":
        return False
    if getattr(x, "subsector", None) == "FORESTRY_REDD":
        return True
    corpus = f"{getattr(x, 'project_title', '')} {getattr(x, 'summary_of_claim', '')}".lower()
    return any(k in corpus for k in ("redd", "avoided deforestation", "kariba", "deforestation rate", "counterfactual"))


def _claim(extracted: ExtractedFilingData, keywords: Tuple[str, ...]) -> Optional[SamplingClaim]:
    return next((c for c in extracted.sampling_claims if any(k in c.parameter.lower() for k in keywords)), None)


def _fmt(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".") if abs(v) < 10 else f"{v:,.1f}"


def _temporal_check(x: ExtractedFilingData, ev: SatelliteEvidence) -> Tuple[ForensicCheckResult, float]:
    r = ev.rainfall
    name = "1. Temporal Confounding Check"
    if x.filing_mode == "EIA_CLEARANCE":
        c = next((claim for claim in x.sampling_claims if is_surface_effluent_concentration(claim)), None) or (x.sampling_claims[0] if x.sampling_claims else None)
        pct = r.sampling_percentile
        day_mm = r.sampling_day_mm
        gov_body = (getattr(c, "governing_body", "") if c else "") or getattr(x, "governing_body", "") or "Statutory Authority"
        unit_str = c.unit if c else "mg/L"
        filed_val = c.claimed_project_value if c else 0.0
        limit_val = c.claimed_control_or_limit_value if (c and c.claimed_control_or_limit_value > 0) else 0.0

        is_conc = is_surface_effluent_concentration(c)
        flood_dilution_trigger = is_conc and (day_mm > 25.0) and (pct >= 90.0)

        claim_txt = (f"{c.parameter}: {_fmt(filed_val)} {unit_str} vs statutory limit {_fmt(limit_val)} "
                     f"{unit_str} ({gov_body}), sampled {c.sampling_date} ({c.page_citation}). Reported as compliant."
                     if c else "No dated effluent sampling claim disclosed.")

        if flood_dilution_trigger:
            status = "FAILED" if pct >= 95 else "WARNING"
            sat = (f"CHIRPS: {day_mm:.1f} mm on {r.sampling_date} — {pct:.1f}th percentile of the 2009–2024 "
                   f"daily record (p95 = {r.p95_mm:.1f} mm); 7-day antecedent rainfall {r.antecedent_7d_mm:.0f} mm.")
            if r.bigquery_corroboration:
                sat += f" Corroborated: {r.bigquery_corroboration}."
            corrected = filed_val * r.estimated_dilution_factor
            ratio = corrected / limit_val if limit_val > 0 else 1.0
            if ratio > 1.0:
                disc = (f"Sampled on a {pct:.0f}th-percentile flood day ({day_mm:.1f} mm rain) → ~{r.estimated_dilution_factor:.1f}× runoff "
                        f"dilution. Dilution-corrected {c.parameter.split('(')[0].strip()}: {_fmt(corrected)} {unit_str} = "
                        f"{ratio:.1f}× the permissible threshold under {gov_body} standards.")
            else:
                disc = (f"Sampled during heavy rainfall ({day_mm:.1f} mm, {pct:.0f}th percentile). "
                        f"Dilution-corrected concentration {_fmt(corrected)} {unit_str} remains within statutory permissible threshold.")
            penalty = max(0.0, pct - 95) * 1.5
        else:
            status = "PASSED"
            penalty = 0.0
            if day_mm == 0.0:
                sat = "Earth Engine climate data (CHIRPS) confirms sampling occurred during normal dry-weather conditions (0.0 mm rain). No flood dilution detected. Baseline is representative."
            elif pct < 75.0:
                sat = f"Earth Engine climate data (CHIRPS) confirms sampling occurred during normal dry-weather conditions ({day_mm:.1f} mm rain). No flood dilution detected. Baseline is representative."
            else:
                sat = f"Earth Engine climate data (CHIRPS) records {day_mm:.1f} mm rainfall ({pct:.0f}th percentile). No flood dilution detected. Baseline is representative."
            if r.bigquery_corroboration:
                sat += f" Corroborated: {r.bigquery_corroboration}."

            if not is_conc:
                disc = f"Parameter '{c.parameter if c else 'Metric'}' is not a surface effluent concentration metric (logarithmic or groundwater scale). No flood dilution applies. Within statutory permissible threshold."
            else:
                disc = f"Sampling occurred under normal conditions ({day_mm:.1f} mm rain, {pct:.0f}th percentile). Within statutory permissible threshold."
        return ForensicCheckResult(check_name=name, status=status, pdf_claim_summary=claim_txt,
                                   satellite_truth_summary=sat, statistical_discrepancy=disc), penalty
    else:
        n = ev.ndvi
        art = n.seasonal_artifact_pct
        sensor_short = get_sensor_label(x, ev, short=True)
        sensor_full = get_sensor_label(x, ev)

        if is_redd_project(x):
            c_defor = _claim(x, ("deforestation", "loss rate", "baseline", "counterfactual", "redd"))
            defor_val = c_defor.claimed_control_or_limit_value if (c_defor and c_defor.claimed_control_or_limit_value > 0) else (c_defor.claimed_project_value if c_defor else 3.9)
            unit_str = c_defor.unit if c_defor else "%/yr"
            sat_loss = getattr(ev.buffer, "baseline_loss_rate_pct", 0.4) if hasattr(ev, "buffer") else 0.4
            sampling_dt = c_defor.sampling_date if c_defor else n.measurement_date
            citation = c_defor.page_citation if c_defor else "Filing Baseline"

            claim_txt = (f"Reported Avoided Deforestation Baseline: {_fmt(defor_val)} {unit_str} "
                         f"counterfactual loss rate, sampled {sampling_dt} ({citation}).")
            sat = (f"{sensor_short}: Historical satellite-derived baseline loss of {sat_loss:.1f}{unit_str} "
                   f"(surface reflectance re-analysis, baseline NDVI {n.baseline_ndvi:.2f} on {n.baseline_date}).")
            if (defor_val - sat_loss) >= 1.5 or art >= 50:
                status = "FAILED" if (defor_val - sat_loss) >= 2.0 or art >= 50 else "WARNING"
                disc = (f"Reported Avoided Deforestation Baseline ({_fmt(defor_val)}{unit_str}) inflated against satellite-derived "
                        f"historical baseline loss ({sat_loss:.1f}{unit_str}) — +{defor_val - sat_loss:.1f}%/yr baseline discrepancy.")
                penalty = max(0.0, (defor_val - sat_loss) * 10.0)
            else:
                status = "PASSED"
                disc = f"Reported Avoided Deforestation Baseline ({_fmt(defor_val)}{unit_str}) corroborated by historical satellite loss ({sat_loss:.1f}{unit_str})."
                penalty = 0.0
        else:
            status = "FAILED" if (art >= 50 and n.claimed_gain_pct >= 8) else "WARNING" if art >= 25 else "PASSED"
            c = _claim(x, ("ndvi", "canopy", "biomass", "forest"))
            claim_txt = (f"{c.parameter}: {_fmt(c.claimed_project_value)} {c.unit} vs baseline {_fmt(c.claimed_control_or_limit_value)} "
                         f"{c.unit}, measured {c.sampling_date} ({c.page_citation})." if c
                         else "Canopy gain claimed without a dated NDVI/biomass measurement.")
            sat = (f"{sensor_short}: baseline NDVI {n.baseline_ndvi:.2f} on {n.baseline_date} (dry season) vs {n.measurement_ndvi:.2f} "
                   f"on {n.measurement_date}, immediately after {r.antecedent_7d_mm:.0f} mm of rain in 7 days "
                   f"({r.sampling_percentile:.0f}th pct). Season-matched baseline NDVI = {n.like_season_baseline_ndvi:.2f}.")
            disc = (f"Claimed canopy gain +{n.claimed_gain_pct:.1f}% collapses to +{n.true_gain_pct:.1f}% when season-matched — "
                    f"{art:.0f}% of the reported gain is a wet-season greening artifact.")
            penalty = max(0.0, art - 50) * 0.15

    return ForensicCheckResult(check_name=name, status=status, pdf_claim_summary=claim_txt,
                               satellite_truth_summary=sat, statistical_discrepancy=disc), penalty


def _spatial_check(x: ExtractedFilingData, ev: SatelliteEvidence) -> Tuple[ForensicCheckResult, float]:
    name = "2. Spatial Control-Group Check"
    if x.filing_mode == "EIA_CLEARANCE":
        w = ev.wind
        if w is None:
            return ForensicCheckResult(check_name=name, status="WARNING",
                                       pdf_claim_summary="No ambient air-quality sensor coordinates disclosed.",
                                       satellite_truth_summary="ERA5-Land wind analysis not possible without sensor siting.",
                                       statistical_discrepancy="Undisclosed sensor geometry — representativeness unverifiable."), 0.0
        c = _claim(x, ("pm", "air", "so2", "no2", "ambient"))
        status = "FAILED" if w.sensor_is_upwind else "WARNING" if w.angular_offset_deg < 90 else "PASSED"
        toward = sum(f for b, f in zip(w.direction_bins_deg, w.frequency_pct)
                     if min(abs(b - w.sensor_bearing_from_source_deg) % 360, 360 - abs(b - w.sensor_bearing_from_source_deg) % 360) <= 34)
        claim_txt = (f"Baseline sensor '{w.sensor_label}' presented as representative ambient monitor"
                     + (f": {c.parameter} {_fmt(c.claimed_project_value)} {c.unit} vs standard {_fmt(c.claimed_control_or_limit_value)} "
                        f"{c.unit} ({c.page_citation})." if c else "."))
        sat = (f"ERA5-Land: wind blew FROM {w.prevailing_from_deg:.0f}° ({compass(w.prevailing_from_deg)}) at "
               f"{w.mean_speed_ms:.1f} m/s around {w.sampling_date}. Sensor sits {w.sensor_distance_km:.1f} km at bearing "
               f"{w.sensor_bearing_from_source_deg:.0f}° ({compass(w.sensor_bearing_from_source_deg)}) from {w.source_label} — "
               f"only {w.angular_offset_deg:.0f}° off the upwind axis.")
        disc = (f"Sensor is {w.sensor_distance_km:.1f} km UPWIND of the emission source: {toward:.0f}% of hours carried clean "
                f"inflow air from the sensor toward the stack. The plume was never sampled."
                if w.sensor_is_upwind else
                f"Sensor offset {w.angular_offset_deg:.0f}° from upwind axis; partial plume capture likely.")
        penalty = max(0.0, 45 - w.angular_offset_deg) * 0.15 if w.sensor_is_upwind else 0.0
    else:
        proj = next((t for t in ev.terrain if t.role == "PROJECT_SITE"), ev.terrain[0] if ev.terrain else None)
        ctrl = next((t for t in ev.terrain if t.role == "CONTROL_SITE"), None)
        if ctrl is None or proj is None:
            return ForensicCheckResult(check_name=name, status="WARNING",
                                       pdf_claim_summary="No reference/control plot coordinates disclosed.",
                                       satellite_truth_summary="SRTM comparability test not possible.",
                                       statistical_discrepancy="Counterfactual baseline is unverifiable."), 0.0

        ctrl_elev = ctrl.claimed_elevation_m
        if (ctrl_elev is None or ctrl_elev == 0.0) and ctrl.srtm_elevation_m > 50.0:
            finding_text = f"Baseline Topography Not Disclosed — Relying on Independent Satellite DEM (SRTM: {ctrl.srtm_elevation_m:.0f}m, {ctrl.slope_deg:.1f}° Slope)"
            return ForensicCheckResult(
                check_name=name,
                status="PASSED",
                pdf_claim_summary="Baseline elevation omitted in filing (0 m or undisclosed).",
                satellite_truth_summary=f"SRTM DEM: control slope {ctrl.slope_deg:.1f}° at {ctrl.srtm_elevation_m:.0f} m elevation.",
                statistical_discrepancy=finding_text,
            ), 0.0

        ds, de = ctrl.slope_deg - proj.slope_deg, ctrl.srtm_elevation_m - (ctrl_elev or 0.0)
        road = ctrl.distance_to_road_km
        near_road = road is not None and road < 0.5
        status = "FAILED" if (abs(ds) >= 15 or near_road) else "WARNING" if (abs(ds) >= 7 or abs(de) > 150) else "PASSED"
        c = _claim(x, ("deforestation", "baseline", "control", "loss rate"))
        ctrl_claimed_str = f"{ctrl.claimed_elevation_m:.0f} m" if (ctrl.claimed_elevation_m is not None and ctrl.claimed_elevation_m > 0) else "undisclosed"
        claim_txt = (f"Reference control plot '{ctrl.label}' declared topographically comparable "
                     f"(claimed elevation {ctrl_claimed_str})."
                     + (f" {c.parameter}: {_fmt(c.claimed_control_or_limit_value)} {c.unit} in control vs "
                        f"{_fmt(c.claimed_project_value)} {c.unit} in project ({c.page_citation})." if c else ""))
        sat = (f"SRTM 30 m: control slope {ctrl.slope_deg:.1f}° at {ctrl.srtm_elevation_m:.0f} m vs project "
               f"{proj.slope_deg:.1f}° at {proj.srtm_elevation_m:.0f} m."
               + (f" Control plot edge is {road * 1000:.0f} m from an active logging highway." if road is not None else ""))
        disc = (f"Control is {ds:+.0f}° steeper and {de:+.0f} m higher"
                + (" beside a haul road" if near_road else "")
                + f" (claimed elevation off by {ctrl.srtm_elevation_m - (ctrl.claimed_elevation_m or 0.0):+.0f} m) — a non-comparable, "
                  "high-risk counterfactual that inflates baseline deforestation and credited avoided emissions.")
        penalty = max(0.0, abs(ds) - 15) * 0.5
    return ForensicCheckResult(check_name=name, status=status, pdf_claim_summary=claim_txt,
                               satellite_truth_summary=sat, statistical_discrepancy=disc), penalty


def _buffer_check(x: ExtractedFilingData, ev: SatelliteEvidence) -> Tuple[ForensicCheckResult, float]:
    name = "3. Perimeter & Buffer Truth Check"
    b = ev.buffer
    sensor_short = get_sensor_label(x, ev, short=True)
    sensor_full = get_sensor_label(x, ev)
    rings_txt = "; ".join(f"{r.label} {r.change_pct:+.0f}%" for r in b.rings)
    if x.filing_mode == "CARBON_OFFSET":
        peak = b.peak_change_pct
        status = "FAILED" if peak >= 25 else "WARNING" if peak >= 10 else "PASSED"
        c = _claim(x, ("leakage",))
        claim_txt = (f"{c.parameter}: {_fmt(c.claimed_project_value)} {c.unit} claimed vs threshold "
                     f"{_fmt(c.claimed_control_or_limit_value)} {c.unit} ({c.page_citation})." if c
                     else "Filing asserts no activity-shifting leakage outside the project boundary.")
        sat = (f"Hansen GFC + {sensor_short} ring analysis ({b.pre_period} vs {b.post_period}): {rings_txt}.")
        disc = (f"{peak:+.0f}% canopy-loss surge in '{b.peak_ring_label}' with the hotspot "
                f"{b.hotspot_distance_outside_km:.1f} km outside the perimeter — deforestation displaced, not avoided."
                if status != "PASSED" else f"Buffer-ring loss within ±10% of pre-project trend (peak {peak:+.0f}%).")
        penalty = max(0.0, peak - 25) * 0.4
    else:
        notes = " ".join(x.extracted_methodology_notes).lower()
        claimed_barren = any(k in notes for k in ("barren", "scrub", "wasteland", "degraded", "low ecological"))
        inside = b.rings[0].mean_ndvi if b.rings else ev.ndvi.project_site_current_ndvi
        riparian = max((r.mean_ndvi for r in b.rings[1:]), default=inside)
        status = ("FAILED" if claimed_barren and inside >= 0.5 else
                  "WARNING" if claimed_barren and inside >= 0.35 else
                  "WARNING" if b.peak_change_pct >= 25 else "PASSED")
        quote = next((n for n in x.extracted_methodology_notes
                      if any(k in n.lower() for k in ("barren", "scrub", "wasteland", "degraded"))), None)
        claim_txt = (f"Project footprint & buffer classified as low-value land: {quote}" if quote
                     else "No land-classification claim for the project footprint.")
        sat = (f"{sensor_short} NDVI: footprint {inside:.2f}, buffer rings up to {riparian:.2f}. "
               f"Hansen canopy-loss change ({b.pre_period} vs {b.post_period}): {rings_txt}.")
        disc = (f"NDVI {inside:.2f}–{riparian:.2f} is inconsistent with 'barren' (<0.20) or 'scrub' (0.20–0.35): "
                "footprint is moderately vegetated riparian habitat requiring ecological survey."
                if claimed_barren and inside >= 0.35 else
                f"Land classification consistent with NDVI {inside:.2f}.")
        penalty = 0.0
    return ForensicCheckResult(check_name=name, status=status, pdf_claim_summary=claim_txt,
                               satellite_truth_summary=sat, statistical_discrepancy=disc), penalty


def _score_and_verdict(checks: List[ForensicCheckResult], extra_penalty: float, x: Optional[ExtractedFilingData] = None) -> Tuple[int, str]:
    if x and x.filing_mode == "EIA_CLEARANCE":
        c = next((claim for claim in x.sampling_claims if is_surface_effluent_concentration(claim)), None) or (x.sampling_claims[0] if x.sampling_claims else None)
        if c:
            filed_val = c.claimed_project_value
            limit_val = c.claimed_control_or_limit_value
            if (limit_val <= 0 or filed_val <= limit_val) and checks[0].status == "PASSED":
                score = 98 if all(chk.status == "PASSED" for chk in checks) else 96
                return score, "✅ COMPLIANCE VERIFIED: STATUTORY LIMITS RESPECTED"
    score = 100.0
    for c in checks:
        score -= {"FAILED": 28, "WARNING": 12, "PASSED": 0}[c.status]
    score = int(round(min(100, max(3, score - min(extra_penalty, 8.0)))))
    return score, _verdict_for(score, checks)


def _verdict_for(score: int, checks: List[ForensicCheckResult]) -> str:
    fails = sum(c.status == "FAILED" for c in checks)
    if score <= 45 or fails >= 2:
        return "HIGH SPIN / BASELINE RIGGING DETECTED"
    if score <= 75 or fails >= 1:
        return "MODERATE METHODOLOGICAL BIAS"
    return "VERIFIED BASELINE INTEGRITY"


def _dispatch(x: ExtractedFilingData, ev: SatelliteEvidence, verdict: str, checks: List[ForensicCheckResult]) -> GroundDispatchAction:
    topic = f"projects/{gcp_project() or 'your-gcp-project-id'}/topics/{PUBSUB_TOPIC_ID}"
    lat, lon = ev.recommended_dispatch_lat, ev.recommended_dispatch_lon
    gov_body = getattr(x, "governing_body", "") or "Statutory Authority"
    if verdict in ("✅ COMPLIANCE VERIFIED: STATUTORY LIMITS RESPECTED", "VERIFIED BASELINE INTEGRITY"):
        return GroundDispatchAction(
            pubsub_topic=topic, action_type="COMPLIANCE_CLEARED", priority="ROUTINE",
            target_recipient=f"{gov_body} Compliance Archive & Regulatory Registry",
            recommended_coordinates_lat=lat, recommended_coordinates_lon=lon,
            recommended_time_window="Next scheduled annual verification cycle",
            instruction_directive="⚡ AUTOMATED REGULATORY CLEARANCE ISSUED: Filing meets statutory baseline standards.",
        )
    critical = verdict.startswith("HIGH SPIN")
    failed = [c.check_name.split(". ", 1)[-1] for c in checks if c.status == "FAILED"]
    if x.filing_mode == "CARBON_OFFSET":
        b = ev.buffer
        ctrl = next((t for t in ev.terrain if t.role == "CONTROL_SITE"), None)
        proj = next((t for t in ev.terrain if t.role == "PROJECT_SITE"), None)
        slope_cap = (proj.slope_deg + 5) if proj else 10
        return GroundDispatchAction(
            pubsub_topic=topic, action_type="REGISTRY_ESCROW_HOLD_AND_RANGER_ALERT",
            priority="CRITICAL" if critical else "HIGH",
            target_recipient=f"Carbon Registry Integrity Desk (issuance escrow) + {x.region_name} forest ranger unit",
            recommended_coordinates_lat=lat, recommended_coordinates_lon=lon,
            recommended_time_window="Escrow hold effective on publish; ranger patrol within 72 h; season-matched NDVI "
                                    f"re-measurement in {ev.rainfall.dry_window_suggestion.split(' (')[0]}",
            instruction_directive=(
                f"(1) Freeze issuance of pending credits for '{x.project_title}' pending re-verification "
                f"[failed: {', '.join(failed) or 'methodology review'}]. "
                f"(2) Ranger team to {lat:.4f}, {lon:.4f} ({b.hotspot_distance_outside_km:.1f} km outside perimeter, "
                f"{b.peak_change_pct:+.0f}% canopy loss): geotag fresh stumps, skid trails and log decks. "
                f"(3) Re-establish the control plot on matched terrain (slope ≤ {slope_cap:.0f}°, >2 km from roads)"
                + (f" replacing '{ctrl.label}'" if ctrl else "") + ". "
                "(4) Re-measure canopy NDVI in the same calendar month as the baseline."),
        )
    w = ev.wind
    water = next((p for p in x.coordinates if p.role == "SENSOR_STATION" and
                  any(k in p.label.lower() for k in ("water", "outfall", "effluent", "river"))), None)
    return GroundDispatchAction(
        pubsub_topic=topic, action_type="COUNTER_SAMPLING_DISPATCH", priority="CRITICAL" if critical else "HIGH",
        target_recipient=f"{gov_body} — Regional Environmental Enforcement & Inspection Unit (unannounced inspection)",
        recommended_coordinates_lat=lat, recommended_coordinates_lon=lon,
        recommended_time_window=f"{ev.rainfall.dry_window_suggestion}; 06:00–10:00 local (stable boundary layer, "
                                "peak plume fumigation); require <20th-percentile 7-day antecedent rainfall",
        instruction_directive=(
            "(1) Unannounced counter-sampling — do not notify the proponent. "
            + (f"(2) Deploy calibrated PM2.5/PM10 + SO2 monitor at downwind receptor {lat:.4f}, {lon:.4f} "
               f"({(w.prevailing_from_deg + 180) % 360:.0f}° transport axis) for 72 h. " if w else "")
            + (f"(3) Composite grab samples for Cr-VI (APHA 3500-Cr B) at '{water.label}' "
               f"({water.latitude:.4f}, {water.longitude:.4f}) under dry-weather low flow. " if water else "")
            + "(4) Split samples with chain-of-custody to an accredited lab; upload results to Accent.AI."),
    )


def build_deterministic_report(x: ExtractedFilingData, ev: SatelliteEvidence) -> FullAuditReport:
    if not getattr(x, "is_satellite_verifiable", True):
        checks = [
            ForensicCheckResult(
                check_name="1. Observational Scope Evaluation",
                status="PASSED",
                pdf_claim_summary=f"Project claims emissions displacement via {x.subsector} ({x.project_title}).",
                satellite_truth_summary="Optical Earth observation satellites (Sentinel-2, Landsat, CHIRPS) measure surface vegetative biomass and hydrological features.",
                statistical_discrepancy=x.unsupported_reason or "Methodology relies on electrical grid telemetry and SCADA meters rather than optical land-surface changes.",
            ),
            ForensicCheckResult(
                check_name="2. Telemetry Modality Compatibility",
                status="PASSED",
                pdf_claim_summary=f"Standard cited: {x.governing_statute} under {x.governing_body}.",
                satellite_truth_summary="Requires electrical SCADA telemetry and grid-dispatch metering receipts.",
                statistical_discrepancy="Direct Earth observation sensors incompatible with non-surface emission displacement.",
            ),
            ForensicCheckResult(
                check_name="3. Regulatory Clearance Pathway",
                status="PASSED",
                pdf_claim_summary="Proponent asserted grid-connected operational capacity and power delivery.",
                satellite_truth_summary="Satellite audit safely bypassed; routing to grid telemetry & PPA auditor.",
                statistical_discrepancy="No land-surface contradiction found. Optical verification safely bypassed.",
            ),
        ]
        topic = f"projects/{gcp_project() or 'your-gcp-project-id'}/topics/{PUBSUB_TOPIC_ID}"
        lat = x.coordinates[0].latitude if x.coordinates else (ev.recommended_dispatch_lat if ev else 0.0)
        lon = x.coordinates[0].longitude if x.coordinates else (ev.recommended_dispatch_lon if ev else 0.0)
        dispatch = GroundDispatchAction(
            pubsub_topic=topic,
            action_type="OUT_OF_SCOPE_NOTICE",
            priority="INFORMATIONAL",
            target_recipient=f"{x.governing_body} Registry & Telemetry Auditor",
            recommended_coordinates_lat=lat,
            recommended_coordinates_lon=lon,
            recommended_time_window="Standard Registry Verification Cycle",
            instruction_directive=(
                f"ADVISORY: Project '{x.project_title}' operates under {x.governing_statute}. "
                "Accent.AI evaluates land-surface interaction (canopy density, terrain slope, hydrological dilution). "
                "Grid-connected renewable energy claims require power-purchase agreements and SCADA grid-meter receipts. "
                "Satellite telemetry verification safely bypassed."
            ),
        )
        return FullAuditReport(
            extracted_data=x,
            integrity_score=None,
            verdict_banner="ℹ️ METHODOLOGY OUT OF OBSERVATIONAL SATELLITE SCOPE",
            forensic_checks=checks,
            dispatch_action=dispatch,
        )

    results = [_temporal_check(x, ev), _spatial_check(x, ev), _buffer_check(x, ev)]
    checks = [r[0] for r in results]
    score, verdict = _score_and_verdict(checks, sum(r[1] for r in results), x=x)
    return FullAuditReport(extracted_data=x, integrity_score=score, verdict_banner=verdict,
                           forensic_checks=checks, dispatch_action=_dispatch(x, ev, verdict, checks))


def _evidence_digest(ev: SatelliteEvidence) -> dict:
    """Compact evidence for the LLM (drop long time-series arrays)."""
    d = json.loads(ev.model_dump_json())
    for k in ("dates", "precip_mm"):
        d["rainfall"].pop(k, None)
    for k in ("dates", "ndvi"):
        d["ndvi"].pop(k, None)
    if d.get("wind"):
        d["wind"].pop("direction_bins_deg", None)
        d["wind"].pop("frequency_pct", None)
    return d


def build_audit_report(x: ExtractedFilingData, ev: SatelliteEvidence, use_gemini: bool = True) -> Tuple[FullAuditReport, str]:
    """Deterministic engine first; optional Gemini audit review with guard-rails. Never raises."""
    base = build_deterministic_report(x, ev)
    if not getattr(x, "is_satellite_verifiable", True):
        return base, "Methodology evaluation (Out of observational satellite scope)"
    if not (use_gemini and gemini_available()):
        return base, "Automated satellite audit rules"
    try:
        prompt = (
            "EXTRACTED FILING CLAIMS:\n" + x.model_dump_json(indent=1)
            + "\n\nINDEPENDENT SATELLITE / REANALYSIS EVIDENCE:\n" + json.dumps(_evidence_digest(ev), indent=1)
            + "\n\nPRELIMINARY DETERMINISTIC AUDIT:\n" + base.model_dump_json(indent=1)
            + "\n\nReturn the final FullAuditReport."
        )
        report, engine = _generate_structured(prompt, FullAuditReport, AUDIT_SYSTEM_PROMPT, 0.2)
        if len(report.forensic_checks) != 3:
            raise ValueError("Gemini returned wrong number of checks")
        if base.verdict_banner == "✅ COMPLIANCE VERIFIED: STATUTORY LIMITS RESPECTED":
            verdict = "✅ COMPLIANCE VERIFIED: STATUTORY LIMITS RESPECTED"
            score = max(report.integrity_score, 96)
        else:
            score = int(min(100, max(0, report.integrity_score)))
            verdict = _verdict_for(score, report.forensic_checks)
        # Guard-rails: geospatial dispatch geometry + routing stays deterministic & authoritative.
        dispatch = _dispatch(x, ev, verdict, report.forensic_checks)
        if verdict == "✅ COMPLIANCE VERIFIED: STATUTORY LIMITS RESPECTED":
            dispatch.instruction_directive = "⚡ AUTOMATED REGULATORY CLEARANCE ISSUED: Filing meets statutory baseline standards."
        else:
            dispatch.instruction_directive = report.dispatch_action.instruction_directive or dispatch.instruction_directive
        # Guard-rail: if baseline topography was undisclosed, preserve DEM finding and bypass
        report_checks = list(report.forensic_checks)
        if base.forensic_checks and len(base.forensic_checks) >= 2 and "Baseline Topography Not Disclosed" in base.forensic_checks[1].statistical_discrepancy:
            report_checks[1] = base.forensic_checks[1]
        report = report.model_copy(update=dict(extracted_data=x, integrity_score=score, verdict_banner=verdict,
                                                forensic_checks=report_checks, dispatch_action=dispatch))
        return report, f"Gemini audit review ({engine})"
    except Exception as exc:
        logger.warning("Gemini audit reasoning failed, deterministic report used: %s", exc)
        return base, "Automated satellite audit rules (Gemini review unavailable)"
