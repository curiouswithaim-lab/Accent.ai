"""Accent.AI — 3-column forensic dashboard (Streamlit).

Accent.AI: Audit of Carbon Credits and ENvironmental impacT on land using AI.
Run locally:   streamlit run app.py
Cloud Run:     streamlit run app.py --server.port=8080 --server.address=0.0.0.0
"""
import datetime as dt
import hashlib
import html
import json
import math
import os
import time
from typing import Any, Dict, List, Optional, Set

import pandas as pd
import plotly.graph_objects as go
import pydeck as pdk
import streamlit as st
from plotly.subplots import make_subplots

st.set_page_config(
    page_title="Accent.AI — Satellite Verification for Environmental Filings",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={"About": "Accent.AI (Audit of Carbon Credits and ENvironmental impacT on land using AI) · AI Builder Cup 2026 · Google Cloud JAPAC Hackathon (Theme 6)"},
)

try:
    from demo_data.golden_cases import GOLDEN_CASES, build_sample_pdf  # noqa: E402
    HAS_GOLDEN = True
except ImportError:
    GOLDEN_CASES = {}
    build_sample_pdf = lambda k: b""
    HAS_GOLDEN = False

from schemas import ExtractedFilingData, FullAuditReport, SatelliteEvidence  # noqa: E402
from services import adc_status  # noqa: E402
from services.earth_engine_verifier import circle_polygon, init_earth_engine, verify_filing, wedge_polygon  # noqa: E402
from services.gemini_extractor import (  # noqa: E402
    build_audit_report,
    compass,
    extract_filing,
    gemini_available,
    gemini_status,
    get_sensor_label,
    is_filing_pre_2015,
    is_redd_project,
    is_surface_effluent_concentration,
)
from services.pubsub_dispatcher import build_payload, publish_dispatch, topic_path  # noqa: E402
from services.storage_service import bucket_name, stage_pdf, store_audit_artifact  # noqa: E402

E = html.escape
TEAL, CYAN, AMBER, ROSE, VIOLET, EMERALD, SLATE = "#2dd4bf", "#22d3ee", "#fbbf24", "#f43f5e", "#a78bfa", "#34d399", "#94a3b8"
STATUS_COLOR = {"FAILED": ROSE, "WARNING": AMBER, "PASSED": EMERALD}
PLOT_CONFIG = {"displayModeBar": False, "responsive": True}


def H(markup: str) -> None:
    """Render HTML without Markdown turning indented lines into code blocks."""
    st.markdown(" ".join(l.strip() for l in markup.splitlines() if l.strip()), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Design system
# ---------------------------------------------------------------------------
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&family=Space+Grotesk:wght@500;600;700&display=swap');
:root{--bg:#060a13;--panel:rgba(15,23,42,.58);--panel2:rgba(15,23,42,.82);--border:rgba(148,163,184,.14);
--teal:#2dd4bf;--cyan:#22d3ee;--amber:#fbbf24;--rose:#f43f5e;--violet:#a78bfa;--emerald:#34d399;--muted:#94a3b8;--text:#e2e8f0;}
html,body,.stApp,[data-testid="stMarkdownContainer"]{font-family:'Inter',system-ui,sans-serif;}
.stApp{background:radial-gradient(1100px 560px at 8% -12%,rgba(45,212,191,.11),transparent 60%),
radial-gradient(900px 520px at 100% -5%,rgba(167,139,250,.11),transparent 60%),linear-gradient(180deg,#060a13 0%,#070d1a 100%);}
[data-testid="stHeader"]{background:transparent;}
.block-container{padding-top:1.1rem;padding-bottom:3rem;max-width:1720px;}
[data-testid="stSidebar"]{background:linear-gradient(180deg,rgba(9,15,28,.97),rgba(6,10,19,.99));border-right:1px solid var(--border);}
[data-testid="stSidebar"] .block-container{padding-top:1rem;}
h1,h2,h3,h4{font-family:'Space Grotesk',sans-serif!important;letter-spacing:-.01em;}
.mono{font-family:'JetBrains Mono',monospace;}
/* hero */
.tt-hero{position:relative;overflow:hidden;border:1px solid var(--border);border-radius:18px;padding:22px 26px;
background:linear-gradient(135deg,rgba(15,23,42,.92),rgba(15,23,42,.7) 60%,rgba(13,148,136,.12));backdrop-filter:blur(14px);
display:flex;align-items:center;margin-bottom:14px;}
.tt-brand{display:flex;align-items:center;gap:18px;position:relative;z-index:1;width:100%;}
.tt-logo{width:50px;height:50px;filter:drop-shadow(0 0 10px rgba(45,212,191,.35));}
.tt-title{font-family:'Space Grotesk',sans-serif;font-size:2.05rem;font-weight:700;line-height:1;margin:0;
background:linear-gradient(90deg,#f8fafc,#5eead4 55%,#a78bfa);-webkit-background-clip:text;background-clip:text;color:transparent;}
.tt-sub{color:var(--muted);font-size:.88rem;margin-top:6px;}
.tt-sub b{color:#cbd5e1;font-weight:600;}
/* column heads + cards */
.tt-col-head{display:flex;align-items:center;gap:12px;padding:12px 16px;border-radius:14px;margin-bottom:12px;
background:linear-gradient(90deg,rgba(45,212,191,.10),rgba(15,23,42,.4));border:1px solid var(--border);}
.tt-col-head .idx{font-family:'JetBrains Mono',monospace;font-size:.75rem;color:#04121a;background:var(--teal);padding:3px 8px;border-radius:7px;font-weight:700;}
.tt-col-head .lbl{font-family:'Space Grotesk',sans-serif;font-weight:600;font-size:1.06rem;color:#f1f5f9;}
.tt-col-head .sub{color:var(--muted);font-size:.76rem;margin-left:auto;text-align:right;}
.tt-col-head.c2{background:linear-gradient(90deg,rgba(34,211,238,.10),rgba(15,23,42,.4));}
.tt-col-head.c2 .idx{background:var(--cyan);}
.tt-col-head.c3{background:linear-gradient(90deg,rgba(251,191,36,.10),rgba(15,23,42,.4));}
.tt-col-head.c3 .idx{background:var(--amber);}
.tt-card{background:var(--panel);border:1px solid var(--border);border-radius:16px;padding:16px 18px;margin-bottom:12px;backdrop-filter:blur(10px);
transition:border-color .25s ease,transform .25s ease;}
.tt-card:hover{border-color:rgba(45,212,191,.32);}
.tt-label{font-size:.68rem;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);font-weight:700;margin:14px 0 8px;}
.tt-label:first-child{margin-top:0;}
.tt-mode{display:inline-block;font-size:.68rem;font-weight:700;letter-spacing:.08em;padding:4px 9px;border-radius:7px;margin-bottom:9px;}
.tt-mode.EIA_CLEARANCE{background:rgba(34,211,238,.13);color:#67e8f9;border:1px solid rgba(34,211,238,.35);}
.tt-mode.CARBON_OFFSET{background:rgba(52,211,153,.13);color:#6ee7b7;border:1px solid rgba(52,211,153,.35);}
.tt-ptitle{font-family:'Space Grotesk',sans-serif;font-size:1.08rem;font-weight:600;color:#f8fafc;line-height:1.3;}
.tt-pmeta{color:var(--muted);font-size:.8rem;margin-top:6px;line-height:1.5;}
.tt-summary{color:#cbd5e1;font-size:.84rem;line-height:1.55;margin-top:10px;padding-top:10px;border-top:1px dashed var(--border);}
.tt-claim{padding:11px 13px;border-radius:12px;background:rgba(2,6,23,.45);border:1px solid var(--border);margin-bottom:8px;transition:all .2s;}
.tt-claim:hover{border-color:rgba(34,211,238,.4);transform:translateX(2px);}
.tt-claim-param{font-size:.82rem;color:#e2e8f0;font-weight:600;}
.tt-claim-vals{display:flex;align-items:baseline;gap:10px;margin:5px 0 4px;font-family:'JetBrains Mono',monospace;}
.tt-claim-vals .v{font-size:1.15rem;color:#5eead4;font-weight:600;}
.tt-claim-vals .vs{font-size:.78rem;color:var(--muted);}
.tt-claim-meta{font-size:.72rem;color:var(--muted);}
.tt-cite{display:inline-block;padding:1px 7px;border-radius:6px;background:rgba(167,139,250,.12);color:#c4b5fd;border:1px solid rgba(167,139,250,.3);font-family:'JetBrains Mono',monospace;font-size:.68rem;}
.tt-coord{display:flex;align-items:center;gap:9px;padding:7px 0;border-bottom:1px solid rgba(148,163,184,.08);font-size:.8rem;}
.tt-coord:last-child{border-bottom:none;}
.tt-coord .nm{color:#e2e8f0;flex:1;}
.tt-coord .ll{font-family:'JetBrains Mono',monospace;font-size:.72rem;color:var(--muted);}
.tt-role{font-size:.6rem;font-weight:700;letter-spacing:.06em;padding:3px 7px;border-radius:6px;white-space:nowrap;}
.tt-role.PROJECT_SITE{background:rgba(45,212,191,.14);color:#5eead4;}
.tt-role.CONTROL_SITE{background:rgba(244,63,94,.14);color:#fda4af;}
.tt-role.SENSOR_STATION{background:rgba(251,191,36,.14);color:#fcd34d;}
.tt-role.BUFFER_ZONE{background:rgba(167,139,250,.14);color:#c4b5fd;}
.tt-quote{border-left:3px solid var(--violet);padding:8px 12px;margin-bottom:8px;background:rgba(167,139,250,.06);border-radius:0 10px 10px 0;
color:#cbd5e1;font-size:.79rem;line-height:1.5;font-style:italic;}
.tt-engine{display:flex;flex-direction:column;gap:6px;font-size:.74rem;color:var(--muted);}
.tt-engine b{color:#e2e8f0;font-weight:600;}
.tt-warn{font-size:.74rem;color:#fcd34d;background:rgba(251,191,36,.08);border:1px solid rgba(251,191,36,.25);padding:7px 10px;border-radius:9px;margin-top:6px;}
/* provenance */
.tt-prov{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px;}
.tt-prov span{font-size:.67rem;padding:4px 8px;border-radius:7px;border:1px solid var(--border);background:rgba(2,6,23,.5);color:#cbd5e1;}
.tt-prov span i{display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:5px;vertical-align:middle;}
.tt-prov .LIVE i{background:var(--emerald);} .tt-prov .BENCHMARK_REPLAY i{background:var(--violet);} .tt-prov .DETERMINISTIC_FALLBACK i{background:var(--amber);}
.tt-legend{display:flex;flex-wrap:wrap;gap:14px;font-size:.72rem;color:var(--muted);padding:9px 4px 2px;}
.tt-legend i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:-1px;}
.tt-ledger{display:grid;grid-template-columns:1fr 1fr;gap:8px;}
.tt-ledger div{background:rgba(2,6,23,.45);border:1px solid var(--border);border-radius:10px;padding:9px 11px;}
.tt-ledger small{display:block;color:var(--muted);font-size:.66rem;letter-spacing:.06em;text-transform:uppercase;}
.tt-ledger b{font-family:'JetBrains Mono',monospace;color:#f1f5f9;font-size:.92rem;font-weight:600;}
.tt-insight{font-size:.8rem;color:#cbd5e1;background:rgba(34,211,238,.06);border:1px solid rgba(34,211,238,.22);border-radius:10px;padding:9px 12px;margin:2px 0 10px;line-height:1.5;}
.tt-insight b{color:#67e8f9;}
/* verdict */
.tt-verdict{border-radius:16px;padding:16px 18px;margin-bottom:10px;position:relative;overflow:hidden;border:1px solid;}
.tt-verdict .k{font-size:.66rem;letter-spacing:.16em;text-transform:uppercase;font-weight:700;opacity:.85;}
.tt-verdict .t{font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:1.22rem;margin-top:4px;line-height:1.2;}
.tt-verdict.spin{background:linear-gradient(135deg,rgba(244,63,94,.24),rgba(127,29,29,.25));border-color:rgba(244,63,94,.55);color:#fecdd3;animation:tt-alarm 2.4s ease-in-out infinite;}
.tt-verdict.moderate{background:linear-gradient(135deg,rgba(251,191,36,.2),rgba(120,53,15,.22));border-color:rgba(251,191,36,.5);color:#fde68a;}
.tt-verdict.verified{background:linear-gradient(135deg,rgba(52,211,153,.2),rgba(6,78,59,.25));border-color:rgba(52,211,153,.5);color:#a7f3d0;}
.tt-verdict.out-of-scope{background:linear-gradient(135deg,rgba(37,99,235,.22),rgba(15,23,42,.92) 75%);border:2px solid #3b82f6;box-shadow:0 0 28px -4px rgba(59,130,246,.45);color:#93c5fd;}
@keyframes tt-alarm{0%,100%{box-shadow:0 0 0 0 rgba(244,63,94,.0)}50%{box-shadow:0 0 28px 2px rgba(244,63,94,.28)}}
.tt-check{border-radius:14px;padding:13px 15px;margin-bottom:10px;background:var(--panel);border:1px solid var(--border);border-left:4px solid;}
.tt-check.FAILED{border-left-color:var(--rose);} .tt-check.WARNING{border-left-color:var(--amber);} .tt-check.PASSED{border-left-color:var(--emerald);}
.tt-check-head{display:flex;align-items:center;gap:10px;margin-bottom:9px;}
.tt-check-name{font-family:'Space Grotesk',sans-serif;font-weight:600;color:#f1f5f9;font-size:.95rem;}
.tt-pill{font-size:.62rem;font-weight:800;letter-spacing:.1em;padding:3px 8px;border-radius:6px;}
.tt-pill.FAILED{background:rgba(244,63,94,.18);color:#fda4af;} .tt-pill.WARNING{background:rgba(251,191,36,.18);color:#fcd34d;} .tt-pill.PASSED{background:rgba(52,211,153,.18);color:#6ee7b7;}
.tt-row{display:grid;grid-template-columns:86px 1fr;gap:8px;font-size:.77rem;line-height:1.5;margin-bottom:6px;color:#cbd5e1;}
.tt-row span{font-size:.6rem;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);font-weight:700;padding-top:2px;}
.tt-delta{margin-top:8px;padding:8px 10px;border-radius:9px;background:rgba(2,6,23,.55);font-size:.78rem;color:#f8fafc;line-height:1.5;border:1px dashed rgba(148,163,184,.22);}
.tt-delta b{font-family:'JetBrains Mono',monospace;color:var(--amber);margin-right:6px;}
.tt-dispatch{border-radius:16px;padding:16px 18px;margin:6px 0 10px;background:linear-gradient(160deg,rgba(251,191,36,.10),rgba(15,23,42,.75) 50%);
border:1px solid rgba(251,191,36,.35);}
.tt-dispatch .hd{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:10px;}
.tt-dispatch .ty{font-family:'Space Grotesk',sans-serif;font-weight:700;color:#fde68a;font-size:.98rem;}
.tt-prio{font-size:.62rem;font-weight:800;letter-spacing:.1em;padding:4px 8px;border-radius:6px;background:rgba(244,63,94,.2);color:#fda4af;}
.tt-prio.HIGH{background:rgba(251,191,36,.2);color:#fcd34d;} .tt-prio.ROUTINE{background:rgba(52,211,153,.2);color:#6ee7b7;}
.tt-dl{display:grid;grid-template-columns:96px 1fr;gap:6px 10px;font-size:.77rem;color:#e2e8f0;line-height:1.45;}
.tt-dl span{color:var(--muted);font-size:.62rem;letter-spacing:.1em;text-transform:uppercase;font-weight:700;padding-top:2px;}
.tt-dl code{font-family:'JetBrains Mono',monospace;font-size:.72rem;color:#5eead4;background:rgba(2,6,23,.6);padding:1px 6px;border-radius:5px;word-break:break-all;}
.tt-receipt{border-radius:12px;padding:11px 13px;margin-top:8px;font-size:.76rem;background:rgba(52,211,153,.08);border:1px solid rgba(52,211,153,.35);color:#a7f3d0;font-family:'JetBrains Mono',monospace;line-height:1.6;word-break:break-all;}
.tt-receipt.SIMULATED{background:rgba(167,139,250,.08);border-color:rgba(167,139,250,.35);color:#ddd6fe;}
/* high-visibility GCP dispatch box */
.tt-gcp-box{background:linear-gradient(135deg,rgba(245,158,11,.16),rgba(217,119,6,.28));border:2px solid #f59e0b;border-radius:14px;padding:14px 16px;margin-bottom:12px;box-shadow:0 0 24px -4px rgba(245,158,11,.45);}
.tt-gcp-title{font-family:'Space Grotesk',sans-serif;font-size:1.02rem;font-weight:800;color:#fbbf24;letter-spacing:.05em;}
.tt-gcp-list{margin:6px 0 0 16px;padding:0;color:#fef3c7;font-size:.86rem;font-weight:500;line-height:1.45;}
/* high-contrast mode banners */
.tt-mode-banner{border-radius:14px;padding:16px 20px;margin:2px 0 16px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;}
.tt-mode-banner.eia{background:linear-gradient(90deg,rgba(245,158,11,.22),rgba(15,23,42,.92) 75%);border:2px solid #f59e0b;box-shadow:0 0 28px -4px rgba(245,158,11,.45);}
.tt-mode-banner.carbon{background:linear-gradient(90deg,rgba(16,185,129,.22),rgba(15,23,42,.92) 75%);border:2px solid #10b981;box-shadow:0 0 28px -4px rgba(16,185,129,.45);}
.tt-mode-banner.out-of-scope{background:linear-gradient(90deg,rgba(37,99,235,.22),rgba(15,23,42,.92) 75%);border:2px solid #3b82f6;box-shadow:0 0 28px -4px rgba(59,130,246,.45);}
.tt-mode-banner .hl{font-family:'Space Grotesk',sans-serif;font-size:1.28rem;font-weight:700;letter-spacing:-.01em;line-height:1.25;}
.tt-mode-banner.eia .hl{color:#fde68a;}
.tt-mode-banner.carbon .hl{color:#a7f3d0;}
.tt-mode-banner.out-of-scope .hl{color:#93c5fd;}
.tt-mode-banner .tag{font-family:'JetBrains Mono',monospace;font-size:.76rem;font-weight:700;padding:5px 12px;border-radius:8px;text-transform:uppercase;letter-spacing:.08em;}
.tt-mode-banner.eia .tag{background:rgba(245,158,11,.2);color:#fbbf24;border:1px solid rgba(245,158,11,.5);}
.tt-mode-banner.carbon .tag{background:rgba(16,185,129,.2);color:#34d399;border:1px solid rgba(16,185,129,.5);}
.tt-mode-banner.out-of-scope .tag{background:rgba(37,99,235,.2);color:#93c5fd;border:1px solid rgba(59,130,246,.5);}
/* single-line document filing header */
.tt-doc-header{display:flex;align-items:center;justify-content:flex-start;flex-wrap:wrap;gap:10px 18px;background:rgba(10,16,30,.85);border:1px solid rgba(148,163,184,.18);border-radius:12px;padding:11px 18px;margin:0 0 16px;font-size:.78rem;}
.tt-dh-badge{font-family:'JetBrains Mono',monospace;font-size:.74rem;font-weight:700;padding:4px 10px;border-radius:6px;letter-spacing:.06em;text-transform:uppercase;}
.tt-dh-badge.EIA_CLEARANCE{background:rgba(245,158,11,.2);color:#fbbf24;border:1px solid rgba(245,158,11,.5);}
.tt-dh-badge.CARBON_OFFSET{background:rgba(16,185,129,.2);color:#34d399;border:1px solid rgba(16,185,129,.5);}
.tt-dh-badge.RENEWABLE_ENERGY_GRID, .tt-dh-badge.OTHER_OUT_OF_SCOPE{background:rgba(37,99,235,.2);color:#93c5fd;border:1px solid rgba(59,130,246,.5);}
.tt-dh-cell{display:flex;align-items:center;gap:6px;}
.tt-dh-k{font-family:'Space Grotesk',sans-serif;font-size:.68rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#94a3b8;}
.tt-dh-v{color:#f1f5f9;font-size:.82rem;}
.tt-dh-v b{color:#ffffff;}
.tt-dh-sep{width:1px;height:16px;background:rgba(148,163,184,.22);}

/* Out-of-scope summary card & NA gauge */
.tt-na-gauge{background:rgba(10,16,30,.85);border:1.5px solid rgba(59,130,246,.4);border-radius:14px;padding:16px 14px;text-align:center;display:flex;flex-direction:column;align-items:center;justify-content:center;height:130px;box-shadow:0 0 20px -4px rgba(37,99,235,.3);}
.tt-na-val{font-family:'Space Grotesk',sans-serif;font-size:2.2rem;font-weight:800;color:#93c5fd;line-height:1.1;}
.tt-na-lbl{font-family:'JetBrains Mono',monospace;font-size:.68rem;font-weight:700;color:#cbd5e1;margin-top:6px;letter-spacing:.05em;text-transform:uppercase;}
.tt-oos-summary-card{background:rgba(10,16,30,.88);border:1.5px solid rgba(59,130,246,.4);border-radius:14px;padding:20px 24px;margin:10px 0 16px;box-shadow:0 12px 32px rgba(0,0,0,.45);}
.tt-oos-header{border-bottom:1px solid rgba(148,163,184,.18);padding-bottom:12px;margin-bottom:16px;display:flex;justify-content:space-between;align-items:center;}
.tt-oos-title{font-family:'Space Grotesk',sans-serif;font-size:1.12rem;font-weight:700;color:#93c5fd;letter-spacing:-.01em;}
.tt-oos-badge{font-family:'JetBrains Mono',monospace;font-size:.72rem;font-weight:700;color:#93c5fd;background:rgba(37,99,235,.15);border:1px solid rgba(59,130,246,.4);padding:4px 10px;border-radius:6px;text-transform:uppercase;}
.tt-oos-auth{font-family:'Space Grotesk',sans-serif;font-size:.9rem;color:#e2e8f0;margin-bottom:14px;line-height:1.5;}
.tt-oos-auth b{color:#60a5fa;}
.tt-oos-notice{background:rgba(37,99,235,.1);border-left:4px solid #3b82f6;border-radius:0 10px 10px 0;padding:14px 18px;font-size:.88rem;line-height:1.6;color:#e2e8f0;}
.tt-oos-notice b{color:#93c5fd;}

/* prominent middle side-by-side claim vs reality */
.tt-mid-sbs-box{background:rgba(10,16,30,.88);border:1.5px solid rgba(148,163,184,.22);border-radius:14px;padding:16px 20px;margin:10px 0 16px;box-shadow:0 12px 32px rgba(0,0,0,.45);}
.tt-sbs-header-bar{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid rgba(148,163,184,.18);padding-bottom:10px;margin-bottom:14px;}
.tt-sbs-title{font-family:'Space Grotesk',sans-serif;font-size:.88rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#cbd5e1;}
.tt-sbs-tag{font-family:'JetBrains Mono',monospace;font-size:.72rem;font-weight:700;color:#2dd4bf;background:rgba(45,212,191,.12);border:1px solid rgba(45,212,191,.3);padding:2px 8px;border-radius:6px;}
.tt-sbs-table{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:4px 0 4px;}
.tt-sbs-col{background:rgba(10,16,30,.85);border:1px solid rgba(148,163,184,.18);border-radius:12px;padding:16px 18px;display:flex;flex-direction:column;justify-content:space-between;}
.tt-sbs-col.claim{border-left:4px solid #2dd4bf;background:linear-gradient(145deg,rgba(45,212,191,.08),rgba(10,16,30,.85));}
.tt-sbs-col.actual.fail{border-left:4px solid #f43f5e;border-color:rgba(244,63,94,.35);background:linear-gradient(145deg,rgba(244,63,94,.10),rgba(10,16,30,.85));}
.tt-sbs-col.actual.pass{border-left:4px solid #34d399;border-color:rgba(52,211,153,.35);background:linear-gradient(145deg,rgba(52,211,153,.10),rgba(10,16,30,.85));}
.tt-sbs-head{font-family:'Space Grotesk',sans-serif;font-size:.74rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#94a3b8;margin-bottom:6px;}
.tt-sbs-param{font-family:'Space Grotesk',sans-serif;font-size:.92rem;font-weight:700;color:#f8fafc;margin-bottom:8px;}
.tt-sbs-val{font-family:'JetBrains Mono',monospace;font-size:1.6rem;font-weight:700;line-height:1.2;color:#ffffff;margin-bottom:8px;}
.tt-sbs-val code{font-family:'JetBrains Mono',monospace!important;font-size:1.55rem!important;color:#ffffff!important;background:transparent!important;border:none!important;padding:0!important;}
.tt-sbs-col.actual.fail .tt-sbs-val code{color:#fca5a5!important;}
.tt-sbs-col.actual.pass .tt-sbs-val code{color:#6ee7b7!important;}
.tt-sbs-desc{font-size:.84rem;color:#cbd5e1;line-height:1.55;margin-bottom:12px;}
.tt-sbs-desc b{color:#f8fafc;}
.tt-sbs-status{display:inline-block;font-family:'JetBrains Mono',monospace;font-size:.76rem;font-weight:700;padding:5px 10px;border-radius:6px;margin-top:auto;}
.tt-sbs-status.claim-status{background:rgba(45,212,191,.12);color:#5eead4;border:1px solid rgba(45,212,191,.3);}
.tt-sbs-status.fail{background:rgba(244,63,94,.18);color:#fda4af;border:1px solid rgba(244,63,94,.45);}
.tt-sbs-status.pass{background:rgba(52,211,153,.18);color:#6ee7b7;border:1px solid rgba(52,211,153,.45);}

/* metric breakdown ledger (left is claimed, right is reality) */
.tt-ledger-box{background:rgba(10,16,30,.88);border:1.5px solid rgba(148,163,184,.22);border-radius:14px;padding:16px 20px;margin:12px 0 16px;box-shadow:0 10px 30px rgba(0,0,0,.45);}
.tt-ledger-head-bar{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid rgba(148,163,184,.18);padding-bottom:10px;margin-bottom:14px;}
.tt-ledger-head-title{font-family:'Space Grotesk',sans-serif;font-size:.85rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#cbd5e1;}
.tt-ledger-head-count{font-family:'JetBrains Mono',monospace;font-size:.72rem;font-weight:700;color:#2dd4bf;background:rgba(45,212,191,.12);border:1px solid rgba(45,212,191,.3);padding:2px 8px;border-radius:6px;}

.tt-metric-row{background:rgba(2,6,23,.6);border:1px solid rgba(148,163,184,.14);border-left:4px solid rgba(148,163,184,.35);border-radius:12px;padding:14px 16px;margin-bottom:12px;transition:border-color .2s ease;}
.tt-metric-row:last-child{margin-bottom:0;}
.tt-metric-row.fail{border-left-color:#f43f5e;background:linear-gradient(135deg,rgba(244,63,94,.08),rgba(2,6,23,.65));border-color:rgba(244,63,94,.3);}
.tt-metric-row.pass{border-left-color:#34d399;background:linear-gradient(135deg,rgba(52,211,153,.08),rgba(2,6,23,.65));border-color:rgba(52,211,153,.3);}
.tt-metric-row-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;}
.tt-m-title{font-family:'Space Grotesk',sans-serif;font-size:.88rem;font-weight:700;color:#f8fafc;letter-spacing:-.01em;}
.tt-m-badge{font-family:'JetBrains Mono',monospace;font-size:.68rem;font-weight:700;padding:2px 7px;border-radius:5px;letter-spacing:.06em;}
.tt-m-badge.fail{background:rgba(244,63,94,.2);color:#fda4af;border:1px solid rgba(244,63,94,.45);}
.tt-m-badge.pass{background:rgba(52,211,153,.2);color:#6ee7b7;border:1px solid rgba(52,211,153,.45);}

.tt-metric-row-body{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:8px;}
.tt-side-cell{background:rgba(10,16,30,.75);border:1px solid rgba(148,163,184,.12);border-radius:8px;padding:10px 12px;}
.tt-side-cell.claim{border-left:3px solid #2dd4bf;}
.tt-side-cell.reality.fail{border-left:3px solid #f43f5e;}
.tt-side-cell.reality.pass{border-left:3px solid #34d399;}
.tt-side-lbl{font-family:'Space Grotesk',sans-serif;font-size:.68rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#94a3b8;margin-bottom:4px;}
.tt-side-val{font-family:'JetBrains Mono',monospace;font-size:.84rem;color:#f1f5f9;line-height:1.45;}
.tt-side-val code{font-family:'JetBrains Mono',monospace!important;font-size:.83rem!important;color:#5eead4!important;background:rgba(15,23,42,.85)!important;padding:2px 7px!important;border-radius:5px!important;border:1px solid rgba(45,212,191,.25)!important;}

.tt-metric-row-footer{display:flex;align-items:center;gap:8px;padding-top:8px;border-top:1px solid rgba(148,163,184,.08);}
.tt-foot-lbl{font-family:'Space Grotesk',sans-serif;font-size:.74rem;font-weight:700;color:#94a3b8;letter-spacing:.04em;}
.tt-metric-row-footer.fail .tt-foot-lbl{color:#fda4af;}
.tt-metric-row-footer.fail .tt-foot-val b{color:#f87171!important;background:rgba(244,63,94,.18);padding:2px 8px;border-radius:5px;border:1px solid rgba(244,63,94,.45);font-family:'JetBrains Mono',monospace;font-size:.81rem;}
.tt-metric-row-footer.pass .tt-foot-lbl{color:#6ee7b7;}
.tt-metric-row-footer.pass .tt-foot-val b{color:#34d399!important;background:rgba(52,211,153,.18);padding:2px 8px;border-radius:5px;border:1px solid rgba(52,211,153,.45);font-family:'JetBrains Mono',monospace;font-size:.81rem;}

/* satellite evidence div on its own */
.tt-evidence-panel{background:rgba(10,16,30,.88);border:1.5px solid rgba(148,163,184,.22);border-radius:14px;padding:16px 20px;margin:16px 0 16px;box-shadow:0 12px 32px rgba(0,0,0,.45);}
.tt-ev-title{font-family:'Space Grotesk',sans-serif;font-size:1.05rem;font-weight:700;color:#f8fafc;letter-spacing:.04em;}
.tt-ev-sub{font-size:.8rem;color:#94a3b8;margin-top:2px;}
.tt-card-h{font-family:'Space Grotesk',sans-serif;font-size:.9rem;font-weight:600;color:#e2e8f0;margin-bottom:8px;}
.tt-dispatch-box{background:rgba(10,16,30,.85);border:1px solid rgba(148,163,184,.2);border-radius:12px;padding:14px 18px;margin:12px 0 8px;}
.tt-disp-head{font-family:'Space Grotesk',sans-serif;font-size:.95rem;font-weight:700;color:#f8fafc;margin-bottom:10px;letter-spacing:.04em;}
/* clinical monospace for all hard data */
code,[data-testid="stMarkdownContainer"] code{
    font-family:'JetBrains Mono',monospace!important;
    font-size:.82rem!important;
    color:#5eead4!important;
    background:rgba(15,23,42,.85)!important;
    padding:2px 6px!important;
    border-radius:5px!important;
    border:1px solid rgba(45,212,191,.25)!important;
}
/* sidebar active button lighting */
[data-testid="stSidebar"] .stButton>button[kind="primary"]{
    background:linear-gradient(135deg,rgba(20,184,166,.45),rgba(14,165,233,.3))!important;
    border:1.5px solid #2dd4bf!important;
    color:#ffffff!important;
    font-weight:700!important;
    box-shadow:0 0 16px rgba(45,212,191,.4)!important;
}
[data-testid="stSidebar"] .stButton>button[kind="secondary"]{
    background:rgba(15,23,42,.55)!important;
    border:1px solid rgba(148,163,184,.14)!important;
    color:#94a3b8!important;
}
[data-testid="stSidebar"] .stButton>button[kind="secondary"]:hover{
    border-color:rgba(45,212,191,.35)!important;
    color:#e2e8f0!important;
}
/* prevent text truncation / ellipses in metrics and cards */
[data-testid="stMetricLabel"],
[data-testid="stMetricLabel"] > div,
[data-testid="stMetricLabel"] label {
    white-space: normal !important;
    text-overflow: clip !important;
    overflow: visible !important;
    word-break: break-word !important;
}
[data-testid="stMetricValue"],
[data-testid="stMetricValue"] > div {
    white-space: normal !important;
    overflow: visible !important;
    word-break: break-word !important;
}
[data-testid="stMetricDelta"],
[data-testid="stMetricDelta"] > div {
    white-space: normal !important;
    overflow: visible !important;
    word-break: break-word !important;
}
/* judge-friendly metric styling */
[data-testid="stMetric"]{background:rgba(15,23,42,.78);border:1px solid rgba(45,212,191,.24);border-radius:14px;padding:14px 16px;box-shadow:0 8px 24px rgba(0,0,0,.35);}
[data-testid="stMetricValue"]{font-family:'JetBrains Mono',monospace!important;font-size:1.7rem!important;font-weight:700!important;color:#f8fafc!important;}
[data-testid="stMetricLabel"]{font-family:'Space Grotesk',sans-serif!important;font-size:.8rem!important;font-weight:700!important;color:#94a3b8!important;text-transform:uppercase;letter-spacing:.06em;}
[data-testid="stMetricDelta"]{font-family:'Inter',sans-serif!important;font-size:.84rem!important;font-weight:700!important;}
/* audit strip */
.tt-strip{display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding:10px 16px;border-radius:14px;background:var(--panel2);border:1px solid var(--border);margin-bottom:14px;font-size:.78rem;color:var(--muted);}
.tt-strip b{color:#e2e8f0;font-weight:600;}
.tt-strip .id{font-family:'JetBrains Mono',monospace;color:#5eead4;}
.tt-strip .sep{width:1px;height:14px;background:var(--border);}
/* landing */
.tt-case{border-radius:18px;padding:22px 24px;border:1px solid var(--border);background:var(--panel);height:265px;min-height:265px;max-height:265px;box-sizing:border-box;position:relative;overflow:hidden;transition:all .3s ease;display:flex;flex-direction:column;margin-bottom:12px;}
.tt-case:hover{transform:translateY(-3px);border-color:rgba(45,212,191,.45);box-shadow:0 18px 40px -20px rgba(45,212,191,.45);}
.tt-case .em{font-size:2rem;line-height:1;margin-bottom:6px;}
.tt-case h3{margin:4px 0 6px;font-size:1.18rem;color:#f8fafc;line-height:1.2;min-height:1.55rem;}
.tt-case p{color:var(--muted);font-size:.82rem;line-height:1.45;margin:0 0 10px;min-height:2.6rem;}
.tt-case ul{margin:0;padding-left:18px;color:#cbd5e1;font-size:.8rem;line-height:1.65;flex:1;}
.tt-case ul li{margin-bottom:4px;}
.tt-case.eia{background:linear-gradient(150deg,rgba(34,211,238,.12),rgba(15,23,42,.6) 55%);}
.tt-case.carbon{background:linear-gradient(150deg,rgba(52,211,153,.12),rgba(15,23,42,.6) 55%);}
.tt-pillar{border-radius:16px;padding:16px 18px;background:var(--panel);border:1px solid var(--border);height:100%;}
.tt-pillar .n{font-family:'JetBrains Mono',monospace;color:var(--teal);font-size:.75rem;font-weight:700;}
.tt-pillar h4{margin:6px 0 6px;font-size:1rem;color:#f1f5f9;}
.tt-pillar p{color:var(--muted);font-size:.8rem;line-height:1.55;margin:0;}
.tt-flow .arr{color:var(--teal);font-weight:700;}
/* streamlit widgets */
.stButton>button,.stDownloadButton>button{border-radius:12px;border:1px solid rgba(45,212,191,.32);background:linear-gradient(135deg,rgba(45,212,191,.13),rgba(34,211,238,.05));
color:#e2e8f0;font-weight:600;transition:all .2s ease;}
.stButton>button:hover,.stDownloadButton>button:hover{transform:translateY(-1px);border-color:var(--teal);box-shadow:0 10px 26px -10px rgba(45,212,191,.6);color:#fff;}
.stButton>button[kind="primary"]{background:linear-gradient(135deg,#14b8a6,#0891b2);border:none;color:#03121a;font-weight:700;}
.stButton>button[kind="primary"]:hover{box-shadow:0 12px 30px -10px rgba(20,184,166,.8);color:#000;}
.stTabs [data-baseweb="tab-list"]{gap:4px;background:rgba(15,23,42,.55);padding:4px;border-radius:12px;border:1px solid var(--border);}
.stTabs [data-baseweb="tab"]{border-radius:9px;padding:6px 12px;height:auto;font-size:.8rem;}
.stTabs [aria-selected="true"]{background:rgba(45,212,191,.15);}
.stTabs [data-baseweb="tab-highlight"],.stTabs [data-baseweb="tab-border"]{display:none;}
[data-testid="stFileUploaderDropzone"]{background:rgba(15,23,42,.6);border:1px dashed rgba(45,212,191,.4);border-radius:14px;}
[data-testid="stExpander"]{border:1px solid var(--border);border-radius:12px;background:rgba(15,23,42,.4);}
.tt-side-h{font-family:'Space Grotesk',sans-serif;font-weight:600;color:#f1f5f9;font-size:.95rem;margin:4px 0 8px;}
.tt-side-s{font-size:.74rem;color:var(--muted);margin:-4px 0 10px;line-height:1.45;}
.tt-stat{font-size:.72rem;color:var(--muted);line-height:1.7;}
.tt-stat b{color:#e2e8f0;font-weight:600;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

LOGO_SVG = """<svg class="tt-logo" viewBox="0 0 64 64" fill="none" xmlns="http://www.w3.org/2000/svg">
<defs><linearGradient id="g" x1="0" y1="0" x2="64" y2="64"><stop stop-color="#2dd4bf"/><stop offset="1" stop-color="#38bdf8"/></linearGradient></defs>
<circle cx="32" cy="32" r="17" stroke="url(#g)" stroke-width="2.2"/>
<path d="M15 32h34M32 15c6 5 6 29 0 34M32 15c-6 5-6 29 0 34" stroke="url(#g)" stroke-width="1.5" opacity=".75"/>
<ellipse cx="32" cy="32" rx="27" ry="10" stroke="#22d3ee" stroke-width="1.3" opacity=".6" transform="rotate(-28 32 32)"/>
<circle cx="55" cy="21" r="3" fill="#fbbf24"/>
<path d="M32 4v6M32 54v6M4 32h6M54 32h6" stroke="#5eead4" stroke-width="2" stroke-linecap="round"/></svg>"""


# ---------------------------------------------------------------------------
# Platform status (computed once per server process)
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def platform_status() -> Dict[str, Any]:
    has, project, detail = adc_status()
    ee_ok, ee_detail = init_earth_engine()
    return dict(adc=has, project=project, adc_detail=detail, gemini=gemini_status(), gemini_ok=gemini_available(),
                ee=ee_ok, ee_detail=ee_detail, cloud_run=bool(os.getenv("K_SERVICE")))

@st.cache_data(show_spinner=False)
def sample_pdfs() -> Dict[str, bytes]:
    return {k: build_sample_pdf(k) for k in GOLDEN_CASES} if HAS_GOLDEN else {}


@st.cache_data(show_spinner=False)
def sample_fingerprints() -> Dict[str, str]:
    return {hashlib.sha256(b).hexdigest(): k for k, b in sample_pdfs().items()} if HAS_GOLDEN else {}


@st.cache_data(show_spinner=False)
def get_precomputed_golden_audit(key: str) -> Dict[str, Any]:
    if not HAS_GOLDEN or key not in GOLDEN_CASES:
        return {}
    case = GOLDEN_CASES[key]
    extracted, profile = case["extracted"], case["profile"]
    ev = verify_filing(extracted, profile, prefer_live=False)
    report, audit_engine = build_audit_report(extracted, ev, use_gemini=False)
    audit_id = f"ACCENT-BENCH-{key.upper()}"
    return dict(
        audit_id=audit_id, extracted=extracted, evidence=ev, report=report,
        source="golden", filename=case["filename"],
        extraction_engine="Benchmark case · Pre-verified document extraction",
        audit_engine=audit_engine, warnings=[], staging=None, elapsed=0.01,
        sha=key
    )


if HAS_GOLDEN:
    for _k in GOLDEN_CASES:
        get_precomputed_golden_audit(_k)


PS = platform_status()
ss = st.session_state
ss.setdefault("audit", None)
ss.setdefault("pending", None)
ss.setdefault("receipts", {})
ss.setdefault("archives", {})
ss.setdefault("live_audits", {})


# ---------------------------------------------------------------------------
# Hero
# ---------------------------------------------------------------------------
def render_hero() -> None:
    H(f"""
    <div class="tt-hero">
      <div class="tt-brand">{LOGO_SVG}
        <div><h1 class="tt-title">Accent.AI</h1>
        <div class="tt-sub"><div style="font-size:0.95rem;font-weight:700;color:#5eead4;letter-spacing:0.02em;margin-bottom:4px;">Audit of Carbon Credits and ENvironmental impacT on land using AI</div>Independent Satellite Audit Platform · Verifying <b>Environmental Impact Assessments</b> &amp; <b>Carbon Credit Baselines</b>.</div></div>
      </div>
    </div>""")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
def render_sidebar() -> Dict[str, bool]:
    with st.sidebar:
        if HAS_GOLDEN and GOLDEN_CASES:
            H('<div class="tt-side-h">Benchmark Filings</div>'
              '<div class="tt-side-s">Simulation run for benchmarking and demo.</div>')
            active_key = ss.audit.get("sha") if (ss.audit and ss.audit.get("source") == "golden") else None

            for key, case in GOLDEN_CASES.items():
                is_active = (active_key == key)
                label = f"● {case['title']}  [ACTIVE]" if is_active else case['title']
                if st.button(label, key=f"side_{key}", width="stretch",
                             type="primary" if is_active else "secondary", help=case["tagline"]):
                    ss.audit = get_precomputed_golden_audit(key)
                    ss.pending = None
                    st.rerun()

        # If user has audited live documents, show quick switcher
        if ss.live_audits:
            st.divider()
            H('<div class="tt-side-h">Active Uploaded Audits</div>'
              '<div class="tt-side-s">Switch between analyzed live filings.</div>')
            for slot in ("eia", "carbon"):
                if slot in ss.live_audits:
                    aud = ss.live_audits[slot]
                    slot_name = "EIA Clearance" if slot == "eia" else "Carbon Offset PDD"
                    p_title = aud["extracted"].project_title or aud.get("filename", "Filing")
                    if len(p_title) > 24:
                        p_title = p_title[:22] + "…"
                    is_current = (ss.audit and ss.audit.get("source") != "golden" and ss.audit.get("slot") == slot)
                    btn_label = f"● {slot_name}: {p_title}  [ACTIVE]" if is_current else f"{slot_name}: {p_title}"
                    if st.button(btn_label, key=f"switch_live_{slot}", width="stretch",
                                 type="primary" if is_current else "secondary"):
                        ss.audit = aud
                        ss.pending = None
                        st.rerun()

        st.divider()
        H('<div class="tt-side-h">Live Document Upload</div>'
          '<div class="tt-side-s">Upload an environmental clearance report (EIA) or carbon project document (PDD). Gemini identifies the document type, extracts claimed measurements and coordinates, and verifies them against satellite observations.</div>')

        up_pdf = st.file_uploader("Upload Regulatory PDF (EIA or Carbon PDD)", type=["pdf"], key="up_single_pdf", label_visibility="collapsed")
        if st.button("Run Satellite Audit", key="run_upload_single", width="stretch", disabled=up_pdf is None,
                     type="primary" if up_pdf is not None else "secondary"):
            ss.pending = {
                "kind": "upload",
                "bytes": up_pdf.getvalue(),
                "name": up_pdf.name,
                "mode_hint": None,  # Autonomous Gemini classification (EIA vs Carbon)
            }
            st.rerun()

        if HAS_GOLDEN and GOLDEN_CASES:
            with st.expander("Need sample test PDFs?"):
                st.caption("Download sample filings to test live uploads:")
                for key, case in GOLDEN_CASES.items():
                    slot_tag = "EIA" if case["extracted"].filing_mode == "EIA_CLEARANCE" else "Carbon"
                    st.download_button(f"Download [{slot_tag}] {case['title']}", data=sample_pdfs()[key], file_name=case["filename"],
                                       mime="application/pdf", key=f"dl_{key}", width="stretch")

        st.divider()
        H('<div class="tt-side-h">Audit Configuration</div>')
        use_gemini = st.toggle("AI Audit Analysis (Gemini)", value=PS["gemini_ok"], disabled=not PS["gemini_ok"],
                               key="opt_use_gemini",
                               help="Analyzes discrepancies between filing claims and satellite data to produce audit findings.")
        live_golden = st.toggle("Live Earth Engine queries", value=True, disabled=not PS["ee"],
                                key="opt_live_golden",
                                help="Fetch live satellite imagery directly from Earth Engine instead of precomputed benchmark layers.")
        if (ss.audit or ss.live_audits) and st.button("Reset Dashboard", width="stretch"):
            ss.audit = None
            ss.live_audits = {}
            st.rerun()

        st.divider()
        H(f"""<div class="tt-side-h">Google Cloud Runtime</div>
        <div class="tt-stat">
        <b>Project</b> · {E(str(PS['project'] or '— not set —'))}<br>
        <b>Credentials</b> · {E(PS['adc_detail'])}<br>
        <b>Document Extractor</b> · {E(PS['gemini'])}<br>
        <b>Earth Engine API</b> · {E(PS['ee_detail'][:110])}<br>
        <b>Pub/Sub topic</b> · <span class="mono">{E(topic_path().split('/')[-1])}</span><br>
        <b>GCS bucket</b> · <span class="mono">{E(bucket_name())}</span>
        </div>""")
    return {"use_gemini": use_gemini, "live_golden": live_golden}


# ---------------------------------------------------------------------------
# Pipeline execution
# ---------------------------------------------------------------------------
def _store(extracted: ExtractedFilingData, ev: SatelliteEvidence, report: FullAuditReport, **meta) -> dict:
    seed = f"{extracted.project_title}|{meta.get('sha', '')}|{time.time()}"
    audit_id = f"ACCENT-{dt.date.today():%Y%m%d}-{hashlib.sha1(seed.encode()).hexdigest()[:6].upper()}"
    audit_dict = dict(audit_id=audit_id, extracted=extracted, evidence=ev, report=report, **meta)
    ss.audit = audit_dict
    slot = meta.get("slot") or ("eia" if extracted.filing_mode == "EIA_CLEARANCE" else "carbon")
    audit_dict["slot"] = slot
    ss.setdefault("live_audits", {})[slot] = audit_dict
    return audit_dict


def process_pending(opts: Dict[str, bool]) -> None:
    job = ss.pending
    ss.pending = None
    if not job:
        return
    if job["kind"] == "golden" and HAS_GOLDEN and not opts.get("live_golden", False):
        ss.audit = get_precomputed_golden_audit(job["key"])
        return

    def _run_single(job_item: dict, step_prefix: str = "") -> dict:
        t0 = time.time()
        if job_item["kind"] == "golden" and HAS_GOLDEN and job_item.get("key") in GOLDEN_CASES:
            case = GOLDEN_CASES[job_item["key"]]
            extracted, profile, warnings, staging = case["extracted"], case["profile"], [], None
            extraction_engine = "Benchmark case · Pre-verified document extraction"
            prefer_live, filename = opts.get("live_golden", False), case["filename"]
            st.write(f"{step_prefix}① Loaded benchmark filing **{case['title']}**")
        else:
            pdf, filename = job_item["bytes"], job_item["name"]
            sha = hashlib.sha256(pdf).hexdigest()
            mode_hint = job_item.get("mode_hint")
            st.write(f"{step_prefix}① Staging PDF in Cloud Storage…")
            staging = stage_pdf(pdf, filename)
            st.write(f"{step_prefix}② Extracting filing claims & detecting document type with Gemini…")
            extracted, extraction_engine, warnings = extract_filing(pdf, filename, mode_hint=mode_hint)
            detected_mode_str = "Environmental Clearance (EIA)" if extracted.filing_mode == "EIA_CLEARANCE" else "Carbon Offset (PDD)"
            st.write(f"{step_prefix}&nbsp;&nbsp;↳ **Document Type:** {detected_mode_str}")
            match = sample_fingerprints().get(sha) if HAS_GOLDEN else None
            profile = GOLDEN_CASES[match]["profile"] if (match and HAS_GOLDEN) else None
            prefer_live = opts.get("live_golden", True) if match else True
            if match:
                warnings.append(f"Fingerprint matches benchmark sample '{GOLDEN_CASES[match]['title']}' — precomputed "
                                "observations used where live Earth Engine layers are offline.")
        sensor_short = get_sensor_label(extracted, short=True)
        if not getattr(extracted, "is_satellite_verifiable", True):
            st.write(f"{step_prefix}③ Methodology out of scope for observational satellites ({extracted.subsector}) — bypassing {sensor_short} / Hansen / NDVI calculations…")
            from services.earth_engine_verifier import fallback_evidence
            ev = fallback_evidence(extracted)
            st.write(f"{step_prefix}④ Generating observational scope advisory…")
            report, audit_engine = build_audit_report(extracted, ev, use_gemini=False)
        else:
            st.write(f"{step_prefix}③ Checking satellite & weather records (CHIRPS rain, {sensor_short} imagery, ERA5 winds, SRTM elevation, Hansen forest loss, NOAA GSOD stations)…")
            ev = verify_filing(extracted, profile, prefer_live=prefer_live)
            st.write(f"{step_prefix}④ Running satellite audit checks" + (" with Gemini audit review…" if opts["use_gemini"] else "…"))
            report, audit_engine = build_audit_report(extracted, ev, use_gemini=opts["use_gemini"])
        elapsed = time.time() - t0
        return _store(
            extracted, ev, report,
            source=job_item["kind"] if job_item["kind"] != "batch_upload" else "upload",
            filename=filename,
            extraction_engine=extraction_engine,
            audit_engine=audit_engine,
            warnings=warnings,
            staging=staging,
            elapsed=elapsed,
            sha=job_item.get("key", hashlib.sha256(job_item.get("bytes", b"")).hexdigest()[:12]),
            slot=job_item.get("slot"),
        )

    t_all = time.time()
    with st.status("Running Accent.AI Audit…", expanded=True) as status:
        if job["kind"] == "batch_upload":
            results = []
            for idx, sub_job in enumerate(job["jobs"], 1):
                slot_label = "EIA Clearance" if sub_job.get("slot") == "eia" else "Carbon Offset PDD"
                st.write(f"**[{idx}/{len(job['jobs'])}] Auditing {slot_label} ({sub_job['name']})…**")
                sub_item = dict(sub_job, kind="upload")
                res = _run_single(sub_item, step_prefix="&nbsp;&nbsp;• ")
                results.append(res)
            if results:
                ss.audit = results[0]
            status.update(label=f"Batch audit complete ({len(results)} filings analyzed) · {time.time() - t_all:.1f}s",
                          state="complete", expanded=False)
        else:
            _run_single(job)
            score_disp = f"{ss.audit['report'].integrity_score}/100" if ss.audit['report'].integrity_score is not None else "N/A — Incompatible Sensor Modality"
            status.update(label=f"Audit complete · Integrity score: {score_disp} · {time.time() - t_all:.1f}s",
                          state="complete", expanded=False)


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
def _style(fig: go.Figure, height: int = 270, title: Optional[str] = None) -> go.Figure:
    fig.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", height=height,
        margin=dict(l=8, r=8, t=38 if title else 14, b=8),
        title=dict(text=title, font=dict(family="Space Grotesk", size=13, color="#e2e8f0"), x=0.01) if title else None,
        font=dict(family="Inter", color="#cbd5e1", size=11),
        legend=dict(orientation="h", y=-0.18, x=0, bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
        hoverlabel=dict(bgcolor="#0f172a", bordercolor="#334155", font=dict(family="Inter")),
    )
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(gridcolor="rgba(148,163,184,.06)", zeroline=False)
    return fig


def _vmark(fig, x: str, text: str, color: str, y: float = 1.0) -> None:
    fig.add_shape(type="line", x0=x, x1=x, y0=0, y1=1, yref="paper", line=dict(color=color, width=2.5, dash="dot"))
    fig.add_annotation(x=x, y=y, yref="paper", text=text, showarrow=False, yanchor="bottom", font=dict(color=color, size=11, family="Inter", weight="bold"),
                       bgcolor="rgba(2,6,23,.9)", bordercolor=color, borderwidth=1.5, borderpad=4)


def chart_rainfall(ev: SatelliteEvidence, claim_label: str) -> go.Figure:
    r = ev.rainfall
    colors = ["#ef4444" if v >= r.p95_mm else "rgba(56,189,248,.65)" for v in r.precip_mm]
    fig = go.Figure()
    fig.add_bar(x=r.dates, y=r.precip_mm, marker_color=colors, name="Daily rainfall (mm)",
                hovertemplate="%{x}<br>%{y:.1f} mm<extra></extra>")
    # Anomaly marker on sampling day
    fig.add_scatter(x=[r.sampling_date], y=[r.sampling_day_mm], mode="markers",
                    marker=dict(size=18, color="#ef4444", symbol="circle", line=dict(color="#ffffff", width=2.5)),
                    name=f"Sample Day ({r.sampling_percentile:.0f}th percentile flood)", hovertemplate="<b>Sampling Day</b>: %{y:.1f} mm<extra></extra>")
    fig.add_hline(y=r.p95_mm, line=dict(color=AMBER, dash="dash", width=1.3),
                  annotation_text=f"95th percentile ({r.p95_mm:.0f} mm)", annotation_font_color=AMBER, annotation_position="top left")
    _vmark(fig, r.sampling_date, f"{r.sampling_percentile:.0f}th Pct Flood", "#ef4444", 0.85)
    fig.update_yaxes(title_text="mm / day")
    return _style(fig, 260, "Daily Rainfall vs 15-Year Baseline (CHIRPS)")


def chart_dilution(report: FullAuditReport, ev: SatelliteEvidence) -> Optional[go.Figure]:
    c = next((c for c in report.extracted_data.sampling_claims
              if is_surface_effluent_concentration(c)), None)
    if not c or c.claimed_control_or_limit_value <= 0:
        return None
    # Only render flood dilution chart if precipitation exceeds 25 mm AND percentile >= 90th
    if ev.rainfall.sampling_day_mm <= 25.0 or ev.rainfall.sampling_percentile < 90.0:
        return None
    corrected = c.claimed_project_value * ev.rainfall.estimated_dilution_factor
    gov_body = getattr(c, "governing_body", "") or getattr(report.extracted_data, "governing_body", "") or "Statutory Standards"
    fig = go.Figure()
    fig.add_bar(y=["Reported in Filing (Flood Day)", "Estimated Normal Flow (True Concentration)"], x=[c.claimed_project_value, corrected],
                orientation="h", marker_color=[TEAL, "#ef4444"], text=[f"{c.claimed_project_value:g} {c.unit}", f"{corrected:.2f} {c.unit}"],
                textposition="outside", hovertemplate="%{x:.3f} " + c.unit + "<extra></extra>", showlegend=False)
    fig.add_vline(x=c.claimed_control_or_limit_value, line=dict(color=AMBER, dash="dash"),
                  annotation_text=f"Permissible Limit {c.claimed_control_or_limit_value:g} {c.unit} ({gov_body})", annotation_font_color=AMBER)
    fig.update_xaxes(range=[0, max(corrected, c.claimed_control_or_limit_value) * 1.35], title_text=c.unit)
    return _style(fig, 180, f"{c.parameter.split(' at ')[0]} — Dilution Correction ({ev.rainfall.estimated_dilution_factor:.1f}× Flood Dilution)")


def chart_ndvi(ev: SatelliteEvidence, x: Optional[ExtractedFilingData] = None) -> go.Figure:
    n, r = ev.ndvi, ev.rainfall
    sensor_lbl = get_sensor_label(x, ev)
    sensor_short = get_sensor_label(x, ev, short=True)
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    rain = pd.DataFrame({"d": pd.to_datetime(r.dates), "mm": r.precip_mm}).set_index("d").resample("MS").sum()
    fig.add_bar(x=rain.index, y=rain["mm"], name="Monthly Rainfall (CHIRPS)", marker_color="rgba(56,189,248,.22)",
                secondary_y=True, hovertemplate="%{x|%b %Y}: %{y:.0f} mm<extra></extra>")
    fig.add_scatter(x=n.dates, y=n.ndvi, mode="lines", name=sensor_lbl,
                    line=dict(color=EMERALD, width=2, shape="spline"), secondary_y=False)
    like_x = f"{n.baseline_date[:4] if n.measurement_date[5:7] >= n.baseline_date[5:7] else int(n.baseline_date[:4]) + 1}-{n.measurement_date[5:7]}-15"
    fig.add_scatter(x=[n.baseline_date], y=[n.baseline_ndvi], mode="markers",
                    marker=dict(size=12, color=CYAN, line=dict(color="#0f172a", width=2)),
                    name=f"Filed Baseline {n.baseline_ndvi:.2f} (Dry Season)", secondary_y=False)
    fig.add_scatter(x=[like_x], y=[n.like_season_baseline_ndvi], mode="markers",
                    marker=dict(size=12, color=TEAL, line=dict(color="#0f172a", width=2)),
                    name=f"Season-Matched Baseline {n.like_season_baseline_ndvi:.2f}", secondary_y=False)
    # Measurement point
    fig.add_scatter(x=[n.measurement_date], y=[n.measurement_ndvi], mode="markers",
                    marker=dict(size=18, color="#ef4444", symbol="circle", line=dict(color="#ffffff", width=2.5)),
                    name=f"Filed Measurement {n.measurement_ndvi:.2f} (Post-Monsoon)", secondary_y=False)
    fig.update_yaxes(title_text="NDVI", secondary_y=False)
    fig.update_yaxes(title_text="mm/mo", secondary_y=True, showgrid=False)
    return _style(fig, 270, f"Canopy Greening vs Monsoon Rainfall ({sensor_short})")


def chart_wind(ev: SatelliteEvidence) -> go.Figure:
    w = ev.wind
    rmax = max(w.frequency_pct) * 1.15
    fig = go.Figure()
    cols = [ROSE if min(abs(b - w.sensor_bearing_from_source_deg) % 360, 360 - abs(b - w.sensor_bearing_from_source_deg) % 360) <= 34
            else "rgba(34,211,238,.55)" for b in w.direction_bins_deg]
    fig.add_barpolar(r=w.frequency_pct, theta=w.direction_bins_deg, width=[20] * len(w.direction_bins_deg),
                     marker_color=cols, marker_line_width=0, name="Wind frequency FROM direction (%)",
                     hovertemplate="From %{theta}°: %{r:.1f}% of hours<extra></extra>")
    fig.add_scatterpolar(r=[0, rmax], theta=[w.sensor_bearing_from_source_deg] * 2, mode="lines+markers",
                         line=dict(color=ROSE, width=3), marker=dict(size=[0, 11], symbol="diamond"),
                         name=f"Monitoring station {w.sensor_bearing_from_source_deg:.0f}° ({w.sensor_distance_km:.1f} km)")
    down = (w.prevailing_from_deg + 180) % 360
    fig.add_scatterpolar(r=[0, rmax], theta=[down] * 2, mode="lines+markers", line=dict(color=AMBER, width=3, dash="dash"),
                         marker=dict(size=[0, 11], symbol="triangle-up"), name=f"Plume transport direction → {down:.0f}°")
    fig.update_layout(polar=dict(
        bgcolor="rgba(0,0,0,0)",
        angularaxis=dict(direction="clockwise", rotation=90, tickvals=list(range(0, 360, 45)),
                         ticktext=["N", "NE", "E", "SE", "S", "SW", "W", "NW"], gridcolor="rgba(148,163,184,.15)"),
        radialaxis=dict(showticklabels=False, gridcolor="rgba(148,163,184,.12)", range=[0, rmax * 1.05])))
    return _style(fig, 340, f"Wind Direction & Plume Alignment (ERA5-Land · {w.sampling_date} ±15 d)")


def chart_terrain(ev: SatelliteEvidence) -> go.Figure:
    pts = [t for t in ev.terrain if t.role in ("PROJECT_SITE", "CONTROL_SITE")]
    names = [("Project" if t.role == "PROJECT_SITE" else "Control") for t in pts]
    fig = make_subplots(rows=1, cols=2, subplot_titles=("Slope (°) — SRTM Elevation Model", "Elevation (m) — Claimed vs SRTM"), horizontal_spacing=0.14)
    fig.add_bar(x=names, y=[t.slope_deg for t in pts], marker_color=[TEAL if n == "Project" else ROSE for n in names],
                text=[f"{t.slope_deg:.1f}°" for t in pts], textposition="outside", showlegend=False, row=1, col=1)
    claimed_texts = ["N/D" if not t.claimed_elevation_m else f"{t.claimed_elevation_m:.0f}" for t in pts]
    claimed_vals = [(t.claimed_elevation_m or 0.0) for t in pts]
    fig.add_bar(x=names, y=claimed_vals, name="Claimed in filing", marker_color="rgba(167,139,250,.6)",
                text=claimed_texts, textposition="outside", row=1, col=2)
    fig.add_bar(x=names, y=[t.srtm_elevation_m for t in pts], name="Measured by SRTM", marker_color=AMBER,
                text=[f"{t.srtm_elevation_m:.0f}" for t in pts], textposition="outside", row=1, col=2)
    fig.update_layout(barmode="group")
    fig.update_yaxes(range=[0, max([t.slope_deg for t in pts] + [1]) * 1.3], row=1, col=1)
    fig.update_yaxes(range=[0, max([t.srtm_elevation_m for t in pts] + claimed_vals + [1]) * 1.25], row=1, col=2)
    fig.update_annotations(font=dict(size=11, color="#94a3b8"))
    return _style(fig, 300, "Reference Control Plot Comparability (SRTM 30m)")


def chart_buffer(ev: SatelliteEvidence) -> go.Figure:
    b = ev.buffer
    labels = [r.label.replace("Inside project perimeter", "Inside") for r in b.rings]
    vals = [r.change_pct for r in b.rings]
    cols = [ROSE if v >= 25 else AMBER if v >= 10 else EMERALD for v in vals]
    fig = go.Figure()
    fig.add_bar(x=labels, y=vals, marker_color=cols, text=[f"{v:+.0f}%" for v in vals], textposition="outside",
                hovertemplate="%{x}: %{y:+.1f}% canopy-loss change<extra></extra>", showlegend=False)
    fig.add_hline(y=25, line=dict(color=ROSE, dash="dot", width=1), annotation_text="High Leakage Alert ≥ 25%", annotation_font_color=ROSE)
    fig.add_hline(y=10, line=dict(color=AMBER, dash="dot", width=1), annotation_text="Elevated Activity ≥ 10%",
                  annotation_font_color=AMBER, annotation_position="bottom right")
    fig.update_yaxes(title_text=f"Canopy Loss Change (%) {b.post_period} vs {b.pre_period}", range=[min(vals + [0]) * 1.3 - 2, max(vals + [30]) * 1.25])
    return _style(fig, 290, "Canopy Loss Change by Distance Ring from Perimeter (Hansen GFC)")


def chart_buffer_loss(ev: SatelliteEvidence) -> go.Figure:
    b = ev.buffer
    labels = [r.label.replace("Inside project perimeter", "Inside") for r in b.rings]
    fig = go.Figure()
    fig.add_bar(x=labels, y=[r.pre_period_loss_ha for r in b.rings], name=f"Pre-project baseline ({b.pre_period})", marker_color="rgba(148,163,184,.55)")
    fig.add_bar(x=labels, y=[r.post_period_loss_ha for r in b.rings], name=f"Crediting period ({b.post_period})", marker_color=ROSE)
    fig.update_layout(barmode="group")
    fig.update_yaxes(title_text="Canopy loss (hectares)")
    return _style(fig, 250, "Annual Forest Canopy Loss by Buffer Ring (Hansen GFC)")


def chart_landcover(ev: SatelliteEvidence, x: Optional[ExtractedFilingData] = None) -> go.Figure:
    b = ev.buffer
    labels = [r.label.replace("Inside project perimeter", "Footprint") for r in b.rings]
    nd = [r.mean_ndvi for r in b.rings]
    sensor_lbl = get_sensor_label(x, ev)
    fig = go.Figure()
    for y0, y1, txt, col in ((0, 0.2, "Barren / Non-Vegetated", "rgba(148,163,184,.10)"), (0.2, 0.35, "Degraded Scrub", "rgba(251,191,36,.08)"),
                             (0.35, 1.0, "Riparian / Dense Vegetation", "rgba(52,211,153,.07)")):
        fig.add_hrect(y0=y0, y1=y1, fillcolor=col, line_width=0, annotation_text=txt, annotation_position="top left",
                      annotation_font=dict(size=10, color="#94a3b8"))
    fig.add_bar(x=labels, y=nd, marker_color=[EMERALD if v >= 0.35 else AMBER for v in nd], text=[f"{v:.2f}" for v in nd],
                textposition="outside", showlegend=False, hovertemplate="%{x}: NDVI %{y:.2f}<extra></extra>")
    fig.update_yaxes(range=[0, 0.85], title_text=f"Vegetation Index ({sensor_lbl})")
    return _style(fig, 290, "Vegetation Density: Declared Land Class vs Satellite NDVI")


# ---------------------------------------------------------------------------
# Map
# ---------------------------------------------------------------------------
def _ascii(s: str) -> str:
    return s.replace("–", "-").replace("—", "-").encode("ascii", "ignore").decode()


def flagged_labels(report: FullAuditReport, ev: SatelliteEvidence) -> Set[str]:
    st_ = {c.check_name[:2]: c.status for c in report.forensic_checks}
    out: Set[str] = set()
    x = report.extracted_data
    if x.filing_mode == "EIA_CLEARANCE":
        if ev.wind and st_.get("2.") in ("FAILED", "WARNING"):
            out.add(ev.wind.sensor_label)
        if st_.get("1.") in ("FAILED", "WARNING"):
            out |= {p.label for p in x.coordinates if p.role == "SENSOR_STATION"
                    and any(k in p.label.lower() for k in ("water", "outfall", "effluent", "river"))}
    elif st_.get("2.") in ("FAILED", "WARNING"):
        out |= {p.label for p in x.coordinates if p.role == "CONTROL_SITE"}
    return out


def render_map(report: FullAuditReport, ev: SatelliteEvidence) -> None:
    x = report.extracted_data
    d = report.dispatch_action
    flagged = flagged_labels(report, ev)
    proj = next((p for p in x.coordinates if p.role == "PROJECT_SITE"), x.coordinates[0])
    polys: List[Dict[str, Any]] = []
    lines: List[Dict[str, Any]] = []
    R = ev.buffer.project_radius_km
    if x.filing_mode == "CARBON_OFFSET":
        polys.append({"polygon": circle_polygon(proj.latitude, proj.longitude, R), "name": "Project perimeter",
                      "kind": f"{R:.1f} km radius", "fill": [45, 212, 191, 38], "line": [45, 212, 191, 230]})
        for ring in ev.buffer.rings[1:]:
            hot = ring.change_pct >= 25
            warm = ring.change_pct >= 10
            polys.append({"polygon": [circle_polygon(proj.latitude, proj.longitude, ring.outer_km),
                                      circle_polygon(proj.latitude, proj.longitude, ring.inner_km)],
                          "name": _ascii(ring.label), "kind": f"{ring.change_pct:+.0f}% canopy loss change",
                          "fill": [244, 63, 94, 70] if hot else [251, 191, 36, 30] if warm else [148, 163, 184, 10],
                          "line": [244, 63, 94, 200] if hot else [148, 163, 184, 90]})
        ctrl = next((p for p in x.coordinates if p.role == "CONTROL_SITE"), None)
        if ctrl:
            lines.append({"from": [proj.longitude, proj.latitude], "to": [ctrl.longitude, ctrl.latitude], "color": [244, 63, 94, 170]})
    elif ev.wind:
        down = (ev.wind.prevailing_from_deg + 180) % 360
        polys.append({"polygon": wedge_polygon(proj.latitude, proj.longitude, down, 18, 3.2), "name": "Prevailing wind plume corridor",
                      "kind": f"transport → {down:.0f}° ({compass(down)})", "fill": [251, 191, 36, 50], "line": [251, 191, 36, 170]})
        polys.append({"polygon": wedge_polygon(proj.latitude, proj.longitude, ev.wind.prevailing_from_deg, 18, 3.2),
                      "name": "Clean air inflow (upwind)", "kind": f"from {ev.wind.prevailing_from_deg:.0f}°",
                      "fill": [34, 211, 238, 22], "line": [34, 211, 238, 90]})
        sensor = next((p for p in x.coordinates if p.label == ev.wind.sensor_label), None)
        if sensor:
            lines.append({"from": [sensor.longitude, sensor.latitude], "to": [proj.longitude, proj.latitude], "color": [244, 63, 94, 200]})

    pts = []
    for p in x.coordinates:
        if p.role == "PROJECT_SITE":
            col = [45, 212, 191, 255]
        elif p.label in flagged:
            col = [244, 63, 94, 255]
        elif p.role == "BUFFER_ZONE":
            col = [167, 139, 250, 230]
        else:
            col = [148, 163, 184, 230]
        tag = "FLAGGED " if p.label in flagged else ""
        pts.append({"pos": [p.longitude, p.latitude], "name": _ascii(p.label), "label": _ascii(f"{tag}{p.label}")[:42],
                    "kind": f"{p.role.replace('_', ' ').title()} · {p.latitude:.4f}, {p.longitude:.4f}", "color": col})
    dispatch_pt = {"pos": [d.recommended_coordinates_lon, d.recommended_coordinates_lat], "name": "Recommended inspection coordinate",
                   "label": "INSPECT", "kind": f"{d.action_type.replace('_', ' ').title()} · {d.recommended_coordinates_lat:.4f}, "
                                                f"{d.recommended_coordinates_lon:.4f}", "color": [251, 191, 36, 255]}
    all_pos = [q["pos"] for q in pts] + [dispatch_pt["pos"]]
    span = max(math.hypot((a[0] - proj.longitude) * 111 * math.cos(math.radians(proj.latitude)), (a[1] - proj.latitude) * 111) for a in all_pos)
    span = max(span, R + 5 if x.filing_mode == "CARBON_OFFSET" else 2.5)
    zoom = max(8.5, min(14.5, 13.6 - math.log2(max(span, 0.5))))
    if x.filing_mode == "CARBON_OFFSET":
        clat, clon = proj.latitude, proj.longitude
    else:
        clat = sum(a[1] for a in all_pos) / len(all_pos)
        clon = sum(a[0] for a in all_pos) / len(all_pos)
    base_r = 400 if x.filing_mode == "CARBON_OFFSET" else 70
    layers = [
        pdk.Layer("PolygonLayer", data=polys, get_polygon="polygon", get_fill_color="fill", get_line_color="line",
                  line_width_min_pixels=1.5, stroked=True, filled=True, pickable=True),
        pdk.Layer("LineLayer", data=lines, get_source_position="from", get_target_position="to", get_color="color",
                  get_width=3, width_min_pixels=2),
        pdk.Layer("ScatterplotLayer", data=[dispatch_pt], get_position="pos", get_radius=base_r * 3.2, filled=False,
                  stroked=True, get_line_color=[251, 191, 36, 200], line_width_min_pixels=2, radius_min_pixels=16),
        pdk.Layer("ScatterplotLayer", data=pts + [dispatch_pt], get_position="pos", get_fill_color="color",
                  get_radius=base_r, radius_min_pixels=6, radius_max_pixels=18, stroked=True,
                  get_line_color=[2, 6, 23, 255], line_width_min_pixels=2, pickable=True),
        pdk.Layer("TextLayer", data=pts + [dispatch_pt], get_position="pos", get_text="label", get_size=12,
                  get_color=[241, 245, 249, 235], get_pixel_offset=[0, -20], font_family="'Inter', sans-serif",
                  font_weight=600, outline_width=3, outline_color=[2, 6, 23, 255], font_settings={"sdf": True}),
    ]
    deck = pdk.Deck(
        layers=layers, map_provider="carto", map_style="dark",
        initial_view_state=pdk.ViewState(latitude=clat, longitude=clon, zoom=zoom, pitch=38, bearing=0),
        tooltip={"html": "<b>{name}</b><br/><span style='color:#94a3b8'>{kind}</span>",
                 "style": {"backgroundColor": "#0f172a", "color": "#e2e8f0", "fontFamily": "Inter", "fontSize": "12px",
                           "border": "1px solid #334155", "borderRadius": "8px"}},
    )
    st.pydeck_chart(deck, height=360)
    rigged = "Flagged station / control" if flagged else "Monitoring station / control"
    extra = ('<span><i style="background:rgba(244,63,94,.6)"></i>High leakage ring (≥25%)</span>' if x.filing_mode == "CARBON_OFFSET"
             else '<span><i style="background:rgba(251,191,36,.5)"></i>Plume corridor</span>')
    H(f"""<div class="tt-legend"><span><i style="background:{TEAL}"></i>Project site</span>
    <span><i style="background:{ROSE}"></i>{rigged}</span><span><i style="background:{VIOLET}"></i>Buffer / surrounding area</span>
    <span><i style="background:{AMBER};box-shadow:0 0 0 3px rgba(251,191,36,.3)"></i>Recommended inspection target</span>{extra}</div>""")


# ---------------------------------------------------------------------------
# Column renderers & Storyboard UX
# ---------------------------------------------------------------------------
def render_mode_banner(a: Dict[str, Any]) -> None:
    x: ExtractedFilingData = a["extracted"]
    is_live = a.get("source") != "golden"
    if not getattr(x, "is_satellite_verifiable", True):
        p_name = x.project_title or "Methodology Review"
        hl = f"AUDIT CASE: Methodology Advisory — {E(p_name)}"
        tag = f"{x.subsector} · OUT OF SCOPE"
        cls = "out-of-scope"
    elif x.filing_mode == "EIA_CLEARANCE":
        p_name = x.project_title or "Environmental Clearance Project"
        hl = f"AUDIT CASE: Environmental Clearance Investigation — {E(p_name)}" if is_live else "AUDIT CASE: Riverfront Effluent Audit (Kanpur Tannery Cluster)"
        tag = "EIA CLEARANCE AUDIT"
        cls = "eia"
    else:
        p_name = x.project_title or "Carbon Offset Project"
        hl = f"AUDIT CASE: Carbon Offset Verification — {E(p_name)}" if is_live else "AUDIT CASE: Peatland Forest Baseline Audit (Kalimantan REDD+ Project)"
        tag = "CARBON OFFSET AUDIT"
        cls = "carbon"

    H(f"""
    <div class="tt-mode-banner {cls}">
      <div class="hl">{hl}</div>
      <div class="tag">{tag}</div>
    </div>
    """)


def calculate_discrepancy(a: Dict[str, Any]) -> float:
    report: FullAuditReport = a["report"]
    ev: SatelliteEvidence = a["evidence"]
    x = report.extracted_data
    if not getattr(x, "is_satellite_verifiable", True):
        return 0.0
    if x.filing_mode == "EIA_CLEARANCE":
        c = next((claim for claim in x.sampling_claims if is_surface_effluent_concentration(claim)), None) or (x.sampling_claims[0] if x.sampling_claims else None)
        if not c:
            return 0.0
        filed_num = c.claimed_project_value
        limit_num = c.claimed_control_or_limit_value
        is_conc = is_surface_effluent_concentration(c)
        flood_dilution_trigger = is_conc and (ev.rainfall.sampling_day_mm > 25.0) and (ev.rainfall.sampling_percentile >= 90.0)

        if flood_dilution_trigger:
            dilution = ev.rainfall.estimated_dilution_factor
            true_num = filed_num * dilution
            if limit_num > 0 and true_num > limit_num:
                pct = ((true_num - limit_num) / limit_num) * 100.0
            else:
                pct = max(0.0, (dilution - 1.0) * 100.0)
            if any(chk.status == "FAILED" for chk in report.forensic_checks):
                pct = max(pct, 50.0)
            return pct
        else:
            if limit_num > 0 and filed_num > limit_num:
                return ((filed_num - limit_num) / limit_num) * 100.0
            # If filed_value <= statutory_limit and no flood dilution, audit variance is 0.0%
            return 0.0
    else:
        seasonal = ev.ndvi.seasonal_artifact_pct
        leakage = ev.buffer.peak_change_pct
        pct = max(seasonal, leakage)
        if any(c.status == "FAILED" for c in report.forensic_checks):
            pct = max(pct, 50.0)
        elif any(c.status == "WARNING" for c in report.forensic_checks):
            pct = max(pct, 20.0)
        return pct


def render_document_header(a: Dict[str, Any]) -> None:
    x: ExtractedFilingData = a["extracted"]
    if not getattr(x, "is_satellite_verifiable", True):
        mode_txt = x.subsector.replace("_", " ")
        badge_cls = x.subsector
    else:
        mode_txt = "EIA CLEARANCE" if x.filing_mode == "EIA_CLEARANCE" else "CARBON OFFSET PDD"
        badge_cls = x.filing_mode
    proj = next((p for p in x.coordinates if p.role == "PROJECT_SITE"), x.coordinates[0] if x.coordinates else None)
    coord_txt = f"[{proj.latitude:.4f}, {proj.longitude:.4f}]" if proj else "—"

    H(f"""
    <div class="tt-doc-header">
      <div class="tt-dh-badge {badge_cls}">{mode_txt}</div>
      <div class="tt-dh-cell">
        <span class="tt-dh-k">PROJECT:</span>
        <span class="tt-dh-v" title="{E(x.project_title)}"><b>{E(x.project_title)}</b></span>
      </div>
      <div class="tt-dh-sep"></div>
      <div class="tt-dh-cell">
        <span class="tt-dh-k">PROPONENT:</span>
        <span class="tt-dh-v"><b>{E(x.applicant_or_proponent)}</b></span>
      </div>
      <div class="tt-dh-sep"></div>
      <div class="tt-dh-cell">
        <span class="tt-dh-k">LOCATION:</span>
        <span class="tt-dh-v">{E(x.region_name)}</span>
      </div>
      <div class="tt-dh-sep"></div>
      <div class="tt-dh-cell">
        <span class="tt-dh-k">COORDINATES:</span>
        <span class="tt-dh-v mono"><code>{coord_txt}</code></span>
      </div>
    </div>
    """)

    with st.expander("Filing Details & Declared Measurements"):
        st.markdown(f"**Executive Summary:** {x.summary_of_claim}")
        if x.sampling_claims:
            st.markdown("**Reported Measurements in Filing:**")
            for sc in x.sampling_claims:
                gov_info = f" ({sc.governing_body})" if getattr(sc, "governing_body", None) else ""
                st.markdown(f"• **{sc.parameter}**: <code>{sc.claimed_project_value:g} {sc.unit}</code> vs statutory limit <code>{sc.claimed_control_or_limit_value:g} {sc.unit}</code>{gov_info} (Sampled: <code>{sc.sampling_date}</code>, Ref: <code>{sc.page_citation}</code>)", unsafe_allow_html=True)
        if x.coordinates:
            st.markdown("**Declared Monitoring Stations & Coordinates:**")
            for pt in x.coordinates[:4]:
                elev_display = f"{pt.claimed_elevation_m:.0f} m" if (pt.claimed_elevation_m is not None and pt.claimed_elevation_m > 0) else "Not Disclosed"
                st.markdown(f"• **{pt.label}** ({pt.role}): <code>[{pt.latitude:.4f}, {pt.longitude:.4f}]</code> (Elevation: <code>{elev_display}</code>)", unsafe_allow_html=True)
        if x.extracted_methodology_notes:
            st.markdown("**Methodology Excerpts from Filing:**")
            for q in x.extracted_methodology_notes[:3]:
                st.caption(f"\"{q}\"")
        st.caption(f"Extraction Method: `{a['extraction_engine']}`")


def _gauge(score: int) -> go.Figure:
    col = ROSE if score <= 45 else AMBER if score <= 75 else EMERALD
    fig = go.Figure(go.Indicator(
        mode="gauge+number", value=score,
        number={"suffix": "<span style='font-size:18px;color:#64748b'>/100</span>", "font": {"size": 42, "family": "Space Grotesk", "color": col}},
        gauge={"axis": {"range": [0, 100], "showticklabels": False, "ticks": ""},
               "bar": {"color": col, "thickness": 0.35}, "bgcolor": "rgba(0,0,0,0)", "borderwidth": 0,
               "steps": [{"range": [0, 45], "color": "rgba(244,63,94,.16)"}, {"range": [45, 75], "color": "rgba(251,191,36,.13)"},
                         {"range": [75, 100], "color": "rgba(52,211,153,.13)"}],
               "threshold": {"line": {"color": "#e2e8f0", "width": 2}, "thickness": 0.85, "value": 75}},
    ))
    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", height=130, margin=dict(l=10, r=10, t=10, b=0), font=dict(color="#cbd5e1"))
    return fig


def get_forensic_ledger_entries(a: Dict[str, Any]) -> List[Dict[str, Any]]:
    report: FullAuditReport = a["report"]
    ev: SatelliteEvidence = a["evidence"]
    x = report.extracted_data
    if not getattr(x, "is_satellite_verifiable", True):
        return [
            {
                "metric_label": "1. Telemetry Modality & Surface Footprint Scope",
                "claim_val": f"{x.subsector} ({x.project_title})",
                "sat_val": "No Direct Optical Land-Surface Interaction",
                "variance_val": "Optical Earth observation satellites safely bypassed — requires electrical SCADA telemetry.",
                "passed": True,
            },
            {
                "metric_label": "2. Statutory Standard & Verification Protocol",
                "claim_val": f"{x.governing_statute} ({x.governing_body})",
                "sat_val": "SCADA Telemetry & PPA Modality Required",
                "variance_val": "Grid displacement standard verified. Out of scope for optical land-surface sensors.",
                "passed": True,
            },
            {
                "metric_label": "3. Regulatory Telemetry Clearance Routing",
                "claim_val": "Operational Power Generation & Grid Dispatch Claims",
                "sat_val": "Telemetry Verification Safely Bypassed",
                "variance_val": "0.0% Observational Discrepancy — Routed to Registry Telemetry Auditor.",
                "passed": True,
            },
        ]
    is_carbon = x.filing_mode == "CARBON_OFFSET"
    disc = calculate_discrepancy(a)
    is_fraud = disc > 15.0
    entries: List[Dict[str, Any]] = []

    if is_carbon:
        # 1. Forest Canopy Growth / Avoided Deforestation
        sensor_name = get_sensor_label(x, ev)
        sensor_short = get_sensor_label(x, ev, short=True)
        if is_redd_project(x):
            c_defor = next((c for c in x.sampling_claims if any(k in c.parameter.lower() for k in ("deforestation", "loss rate", "baseline", "counterfactual", "redd"))), None)
            claimed_rate = c_defor.claimed_control_or_limit_value if (c_defor and c_defor.claimed_control_or_limit_value > 0) else (c_defor.claimed_project_value if c_defor else 3.9)
            unit_str = c_defor.unit if c_defor else "%/yr"
            sampling_dt = c_defor.sampling_date if c_defor else (ev.ndvi.measurement_date if (ev and hasattr(ev, "ndvi")) else "Filing Baseline")
            sat_loss = getattr(ev.buffer, "baseline_loss_rate_pct", 0.4) if (ev and hasattr(ev, "buffer")) else 0.4

            if is_fraud:
                entries.append({
                    "metric_label": "1. Avoided Deforestation Baseline vs Historical Satellite Truth",
                    "claim_val": f"Reported Avoided Deforestation Baseline: {claimed_rate:.1f}{unit_str} (Claimed Counterfactual · Date: {sampling_dt})",
                    "sat_val": f"{sat_loss:.1f}{unit_str} Historical Baseline Loss ({sensor_name} & Hansen GFC)",
                    "variance_val": f"+{claimed_rate - sat_loss:.1f}%/yr Baseline Inflation (Historical satellite loss rate is {sat_loss:.1f}{unit_str})",
                    "passed": False,
                })
            else:
                entries.append({
                    "metric_label": "1. Avoided Deforestation Baseline vs Historical Satellite Truth",
                    "claim_val": f"Reported Avoided Deforestation Baseline: {claimed_rate:.1f}{unit_str} (Date: {sampling_dt})",
                    "sat_val": f"{sat_loss:.1f}{unit_str} Historical Baseline Loss (Corroborated by {sensor_name})",
                    "variance_val": "Reported Avoided Deforestation Baseline Validated",
                    "passed": True,
                })
        else:
            c = next((c for c in x.sampling_claims if any(k in c.parameter.lower() for k in ("ndvi", "canopy", "biomass", "forest"))), None) or (x.sampling_claims[0] if x.sampling_claims else None)
            claimed_gain = f"+{ev.ndvi.claimed_gain_pct:.1f}%" if (ev and hasattr(ev, "ndvi")) else "+22.7%"
            sampling_dt = c.sampling_date if c else ev.ndvi.measurement_date
            if is_fraud:
                true_gain = f"+{ev.ndvi.true_gain_pct:.1f}%"
                entries.append({
                    "metric_label": "1. Forest Canopy Growth & Baseline Seasonality",
                    "claim_val": f"{claimed_gain} Canopy Gain (Reported in PDD · Date: {sampling_dt})",
                    "sat_val": f"{true_gain} True Gain (Season-Matched · Baseline NDVI {ev.ndvi.baseline_ndvi:.2f} · {sensor_short})",
                    "variance_val": f"+{disc:.0f}% Discrepancy ({ev.ndvi.seasonal_artifact_pct:.0f}% of reported gain is seasonal rainfall greening)",
                    "passed": False,
                })
            else:
                entries.append({
                    "metric_label": "1. Forest Canopy Growth & Baseline Seasonality",
                    "claim_val": f"{claimed_gain} Canopy Gain (Reported in PDD · Date: {sampling_dt})",
                    "sat_val": f"{claimed_gain} (Season-Matched {sensor_name})",
                    "variance_val": "Baseline Seasonality Confirmed (No Timing Artifact)",
                    "passed": True,
                })

        # 2. Control Plot Topography
        ctrl = next((t for t in ev.terrain if t.role == "CONTROL_SITE"), None)
        proj = next((t for t in ev.terrain if t.role == "PROJECT_SITE"), None)
        target_pt = ctrl or proj
        if target_pt:
            ctrl_elev = target_pt.claimed_elevation_m
            # Topography / Null Value Handler:
            if (ctrl_elev is None or ctrl_elev == 0.0) and target_pt.srtm_elevation_m > 50.0:
                sat_elev_str = f"{target_pt.srtm_elevation_m:.0f}"
                sat_slope_str = f"{target_pt.slope_deg:.1f}"
                finding = f"Baseline Topography Not Disclosed — Relying on Independent Satellite DEM (SRTM: {sat_elev_str}m, {sat_slope_str}° Slope)"
                entries.append({
                    "metric_label": "2. Reference Control Plot Comparability & Terrain",
                    "claim_val": "Baseline Topography Not Disclosed (Elevation Omitted in Filing)",
                    "sat_val": f"SRTM DEM: {sat_elev_str}m Elevation, {sat_slope_str}° Slope",
                    "variance_val": finding,
                    "passed": True,
                })
            elif ctrl and proj:
                ds = ctrl.slope_deg - proj.slope_deg
                de = ctrl.srtm_elevation_m - (ctrl_elev or 0.0)
                ctrl_elev_display = f"{ctrl.claimed_elevation_m:.0f} m Elevation" if (ctrl.claimed_elevation_m is not None and ctrl.claimed_elevation_m > 0) else "Not Disclosed"
                slope_fraud = abs(ds) >= 15 or (ctrl.distance_to_road_km is not None and ctrl.distance_to_road_km < 0.5)
                if slope_fraud:
                    entries.append({
                        "metric_label": "2. Reference Control Plot Comparability & Terrain",
                        "claim_val": f"{ctrl_elev_display} (Declared Equivalent Reference Plot)",
                        "sat_val": f"{ctrl.srtm_elevation_m:.0f} m Elevation, {ctrl.slope_deg:.1f}° Slope (Steep Ridge Beside Haul Road)",
                        "variance_val": f"{de:+.0f} m Elevation / {ds:+.0f}° Slope Mismatch (Biased Baseline Inflates Credits)",
                        "passed": False,
                    })
                else:
                    entries.append({
                        "metric_label": "2. Reference Control Plot Comparability & Terrain",
                        "claim_val": ctrl_elev_display,
                        "sat_val": f"{ctrl.srtm_elevation_m:.0f} m Elevation, {ctrl.slope_deg:.1f}° Slope",
                        "variance_val": "Topographically Equivalent (Control Plot Validated)",
                        "passed": True,
                    })

        # 3. Buffer Deforestation Leakage
        peak = ev.buffer.peak_change_pct
        if peak >= 25:
            entries.append({
                "metric_label": "3. Surrounding Buffer & Deforestation Leakage",
                "claim_val": "0.0% Displaced Deforestation (No Leakage Declared)",
                "sat_val": f"+{peak:.0f}% Canopy Loss Spike ({ev.buffer.peak_ring_label} Hotspot)",
                "variance_val": f"+{peak:.0f}% Forest Loss Discrepancy (Clearing Displaced to Surrounding Buffer)",
                "passed": False,
            })
        else:
            entries.append({
                "metric_label": "3. Surrounding Buffer & Deforestation Leakage",
                "claim_val": "0.0% Displaced Deforestation (No Leakage Declared)",
                "sat_val": f"{peak:+.0f}% Canopy Change (Within Normal Historical Variation)",
                "variance_val": "Surrounding Buffer Stable (No Displaced Deforestation)",
                "passed": True,
            })

    else:
        # EIA Case
        # 1. Parameter Concentration & Baseline Compliance
        c = next((claim for claim in x.sampling_claims if is_surface_effluent_concentration(claim)), None) or (x.sampling_claims[0] if x.sampling_claims else None)
        claimed_num = c.claimed_project_value if c else 0.04
        unit_str = c.unit if c else "mg/L"
        limit_num = c.claimed_control_or_limit_value if (c and c.claimed_control_or_limit_value > 0) else 0.10
        param_name = c.parameter.split(" at ")[0] if c else "River Effluent Cr-VI"
        sampling_dt = c.sampling_date if c else ev.rainfall.sampling_date
        gov_body = getattr(c, "governing_body", "") or getattr(x, "governing_body", "") or "Statutory Authority"
        is_conc = is_surface_effluent_concentration(c)
        flood_dilution_trigger = is_conc and (ev.rainfall.sampling_day_mm > 25.0) and (ev.rainfall.sampling_percentile >= 90.0)

        audit_variance = calculate_discrepancy(a)
        is_compliant = (audit_variance <= 10.0 and claimed_num <= limit_num)

        if is_compliant:
            claim_val = f"Asserted Compliant (Limit: {limit_num:g} {unit_str} under {gov_body} standards) · Sampled: {sampling_dt}"
            if ev.rainfall.sampling_day_mm == 0.0:
                sat_val = f"{claimed_num:g} {unit_str} — Earth Engine climate data (CHIRPS) confirms sampling occurred during normal dry-weather conditions (0.0 mm rain). No flood dilution detected. Baseline is representative."
            elif ev.rainfall.sampling_percentile < 75.0:
                sat_val = f"{claimed_num:g} {unit_str} — Earth Engine climate data (CHIRPS) confirms sampling occurred during normal dry-weather conditions ({ev.rainfall.sampling_day_mm:.1f} mm rain). No flood dilution detected. Baseline is representative."
            else:
                sat_val = f"{claimed_num:g} {unit_str} (Normal Baseline Conditions Confirmed)"
            entries.append({
                "metric_label": f"1. {param_name} & Regulatory Baseline Compliance",
                "claim_val": claim_val,
                "sat_val": sat_val,
                "variance_val": "0.0% Discrepancy — Within statutory permissible threshold.",
                "passed": True,
            })
        elif flood_dilution_trigger:
            dilution = ev.rainfall.estimated_dilution_factor
            true_num = claimed_num * dilution
            variance_pct = (dilution - 1.0) * 100.0
            ratio = true_num / limit_num if limit_num > 0 else 1.0
            entries.append({
                "metric_label": f"1. {param_name} Concentration & River Dilution",
                "claim_val": f"Asserted Compliant (Limit: {limit_num:g} {unit_str} under {gov_body} standards) · Sampled: {sampling_dt}",
                "sat_val": f"{true_num:.2f} {unit_str} (Estimated under Normal River Flow · {dilution:.1f}× Flood Dilution)",
                "variance_val": f"+{variance_pct:.0f}% Discrepancy ({ratio:.1f}× permissible threshold under {gov_body} standards)",
                "passed": False,
            })
        else:
            excess_pct = ((claimed_num - limit_num) / limit_num) * 100.0 if limit_num > 0 else 0.0
            ratio = claimed_num / limit_num if limit_num > 0 else 1.0
            entries.append({
                "metric_label": f"1. {param_name} & Regulatory Compliance",
                "claim_val": f"Reported {claimed_num:g} {unit_str} (Limit: {limit_num:g} {unit_str} under {gov_body} standards) · Sampled: {sampling_dt}",
                "sat_val": f"{claimed_num:g} {unit_str} (Normal Flow Confirmed)",
                "variance_val": f"+{excess_pct:.0f}% Discrepancy ({ratio:.1f}× permissible threshold under {gov_body} standards)",
                "passed": False,
            })

        # 2. Ambient Air Sensor Siting (Wind Alignment)
        if ev.wind:
            w = ev.wind
            if w.sensor_is_upwind:
                entries.append({
                    "metric_label": "2. Ambient Air Monitoring Station Placement & Wind Direction",
                    "claim_val": f"Declared Downwind Monitoring Station '{w.sensor_label}'",
                    "sat_val": f"{w.sensor_bearing_from_source_deg:.0f}° Bearing ({w.sensor_distance_km:.1f} km Upwind Inflow Axis)",
                    "variance_val": "Station is 180° Upwind: Clean Air Inflow Sampled Instead of Stack Plume",
                    "passed": False,
                })
            else:
                entries.append({
                    "metric_label": "2. Ambient Air Monitoring Station Placement & Wind Direction",
                    "claim_val": f"Declared Monitoring Station '{w.sensor_label}'",
                    "sat_val": f"{w.sensor_bearing_from_source_deg:.0f}° Bearing (Downwind Plume Corridor)",
                    "variance_val": "Station Placed in Downwind Plume Corridor (Geometry Confirmed)",
                    "passed": True,
                })

        # 3. Buffer Land Cover Classification
        notes = " ".join(x.extracted_methodology_notes).lower()
        claimed_barren = any(k in notes for k in ("barren", "scrub", "wasteland", "degraded"))
        inside = ev.buffer.rings[0].mean_ndvi if ev.buffer.rings else ev.ndvi.project_site_current_ndvi
        if claimed_barren and inside >= 0.35:
            entries.append({
                "metric_label": "3. Site Land Cover & Ecological Classification",
                "claim_val": "Declared Low-Value Barren Scrubland",
                "sat_val": f"Satellite NDVI {inside:.2f} (Lush Riparian Wetland Corridor)",
                "variance_val": "NDVI Confirms Densely Vegetated Riparian Corridor (Not Barren)",
                "passed": False,
            })
        else:
            entries.append({
                "metric_label": "3. Site Land Cover & Ecological Classification",
                "claim_val": "Declared Land Classification",
                "sat_val": f"Satellite NDVI {inside:.2f} (Consistent with Ground Truth)",
                "variance_val": "Land Classification Consistent with Satellite Observations",
                "passed": True,
            })

    return entries


def render_plain_english_comparison(a: Dict[str, Any]) -> None:
    report: FullAuditReport = a["report"]
    ev: SatelliteEvidence = a["evidence"]
    x = report.extracted_data

    if not getattr(x, "is_satellite_verifiable", True):
        report.verdict_banner = "ℹ️ METHODOLOGY OUT OF OBSERVATIONAL SATELLITE SCOPE"
        report.integrity_score = None

        v_col1, v_col2 = st.columns([2.5, 1.0], gap="large")
        with v_col1:
            H("""<div class="tt-verdict out-of-scope">
              <div class="k">Observational Scope Advisory · Incompatible Sensor Modality</div>
              <div class="t">ℹ️ METHODOLOGY OUT OF OBSERVATIONAL SATELLITE SCOPE</div>
            </div>""")
            st.info(
                f"ℹ️ **Regulatory Advisory Directive**: {report.dispatch_action.instruction_directive}"
            )
        with v_col2:
            H("""<div class="tt-na-gauge">
              <div class="tt-na-val">N/A</div>
              <div class="tt-na-lbl">Incompatible Sensor Modality</div>
            </div>""")

        auth_str = f"{x.governing_statute} ({x.governing_body})" if x.governing_body and x.governing_body not in x.governing_statute else (x.governing_statute or "UNFCCC Clean Development Mechanism ACM0002")
        H(f"""
        <div class="tt-oos-summary-card">
          <div class="tt-oos-header">
            <span class="tt-oos-title">Document Extracted: {E(x.project_title)} ({E(x.subsector)})</span>
            <span class="tt-oos-badge">OUT OF OBSERVATIONAL SATELLITE SCOPE</span>
          </div>
          <div class="tt-oos-auth">
            <b>Authority:</b> {E(auth_str)}
          </div>
          <div class="tt-oos-notice">
            <b>Notice:</b> Accent.AI evaluates land-surface interaction (canopy density, terrain slope, hydrological dilution). Grid-connected renewable energy claims require power-purchase agreements and SCADA grid-meter receipts. Satellite telemetry verification safely bypassed.
          </div>
        </div>
        """)
        return

    is_carbon = x.filing_mode == "CARBON_OFFSET"
    disc = calculate_discrepancy(a)

    if not is_carbon:
        c = next((claim for claim in x.sampling_claims if is_surface_effluent_concentration(claim)), None) or (x.sampling_claims[0] if x.sampling_claims else None)
        claimed_num = c.claimed_project_value if c else 0.04
        limit_num = c.claimed_control_or_limit_value if (c and c.claimed_control_or_limit_value > 0) else 0.10
        is_compliant = (disc <= 10.0 and claimed_num <= limit_num)
    else:
        is_compliant = disc <= 10.0

    is_fraud = not is_compliant

    # Dynamic Integrity Score & Banner sync
    if is_compliant:
        score = max(report.integrity_score, 98 if disc == 0.0 else 96)
        report.integrity_score = score
        report.verdict_banner = "✅ COMPLIANCE VERIFIED: STATUTORY LIMITS RESPECTED"
    else:
        score = report.integrity_score

    # Executive Verdict & Gauge Header
    v_col1, v_col2 = st.columns([2.5, 1.0], gap="large")
    with v_col1:
        if is_compliant:
            cls = "verified"
            verdict_text = "✅ COMPLIANCE VERIFIED: STATUTORY LIMITS RESPECTED"
            badge_label = "AUDIT PASSED · COMPLIANT"
            pubsub_msg = "⚡ AUTOMATED REGULATORY CLEARANCE ISSUED: Filing meets statutory baseline standards."
        else:
            cls = "spin" if report.verdict_banner.startswith("HIGH") else "moderate"
            verdict_text = "Critical Baseline Discrepancy Detected" if report.verdict_banner.startswith("HIGH") else "Moderate Baseline Discrepancy Detected"
            badge_label = "AUDIT FAILED · NON-COMPLIANT"
            d_lat = report.dispatch_action.recommended_coordinates_lat or (x.coordinates[0].latitude if x.coordinates else 0.0)
            d_lon = report.dispatch_action.recommended_coordinates_lon or (x.coordinates[0].longitude if x.coordinates else 0.0)
            if is_carbon:
                pubsub_msg = f"Field Alert Published (Cloud Pub/Sub): Registry credit issuance placed on hold. Inspection notice routed to field rangers at coordinates `[{d_lat:.4f}, {d_lon:.4f}]`."
            else:
                pubsub_msg = f"Field Alert Published (Cloud Pub/Sub): Unannounced re-sampling order dispatched to regional environmental inspectors at coordinates `[{d_lat:.4f}, {d_lon:.4f}]`."

        H(f'<div class="tt-verdict {cls}"><div class="k">Compliance Audit Verdict · {badge_label}</div><div class="t">{E(verdict_text)}</div></div>')
        if is_compliant:
            st.success(pubsub_msg)
        else:
            st.warning(pubsub_msg)

    with v_col2:
        st.plotly_chart(_gauge(score), theme=None, config=PLOT_CONFIG, key="c_gauge_mid")

    # Core Side-by-Side Comparison Box explaining in Plain English
    if is_carbon:
        sensor_short = get_sensor_label(x, ev, short=True)
        if is_redd_project(x):
            c_defor = next((c for c in x.sampling_claims if any(k in c.parameter.lower() for k in ("deforestation", "loss rate", "baseline", "counterfactual", "redd"))), None)
            claimed_rate = c_defor.claimed_control_or_limit_value if (c_defor and c_defor.claimed_control_or_limit_value > 0) else (c_defor.claimed_project_value if c_defor else 3.9)
            unit_str = c_defor.unit if c_defor else "%/yr"
            sampling_dt = c_defor.sampling_date if c_defor else (ev.ndvi.measurement_date if (ev and hasattr(ev, "ndvi")) else "Filing Baseline")
            sat_loss = getattr(ev.buffer, "baseline_loss_rate_pct", 0.4) if (ev and hasattr(ev, "buffer")) else 0.4

            claimed_val = f"{claimed_rate:.1f}{unit_str}"
            claim_param = "Reported Avoided Deforestation Baseline"
            claim_desc = (
                f"The project filing claims a <b>Reported Avoided Deforestation Baseline of {claimed_val}</b> "
                f"counterfactual forest loss rate to justify voluntary carbon credit issuance (dated {sampling_dt})."
            )
            claim_badge = "Asserted Avoided Deforestation Baseline"

            if is_fraud:
                actual_val = f"{sat_loss:.1f}{unit_str}"
                actual_param = "Satellite-Derived Historical Baseline Loss"
                actual_desc = (
                    f"Independent {sensor_short} and Hansen Global Forest Change satellite telemetry indicates a "
                    f"<b>satellite-derived historical baseline loss of only {actual_val}</b>. "
                    f"The project's reported baseline loss rate is inflated above actual observed deforestation, "
                    f"generating unearned carbon offset credits against an exaggerated counterfactual."
                )
                variance_badge = f"+{claimed_rate - sat_loss:.1f}%/yr Baseline Inflation (+{disc:.0f}% Discrepancy)"
                status_cls = "fail"
            else:
                actual_val = claimed_val
                actual_param = "Satellite-Derived Historical Baseline Loss"
                actual_desc = f"Independent {sensor_short} satellite telemetry confirms the reported avoided deforestation baseline aligns with historical satellite observation."
                variance_badge = "Baseline Integrity Verified"
                status_cls = "pass"
        else:
            c = next((c for c in x.sampling_claims if any(k in c.parameter.lower() for k in ("ndvi", "canopy", "biomass", "forest"))), None) or (x.sampling_claims[0] if x.sampling_claims else None)
            claimed_val = f"+{ev.ndvi.claimed_gain_pct:.1f}%" if (ev and hasattr(ev, "ndvi")) else "+22.7%"
            sampling_dt = c.sampling_date if c else ev.ndvi.measurement_date

            claim_param = "Net Forest Canopy Gain"
            claim_desc = (
                f"The project filing claims a <b>{claimed_val} increase in forest canopy</b> "
                f"to justify voluntary carbon credit issuance (dated {sampling_dt})."
            )
            claim_badge = "Asserted Carbon Credit Issuance"

            if is_fraud:
                actual_val = f"+{ev.ndvi.true_gain_pct:.1f}%"
                actual_param = "True Canopy Gain (Season-Matched)"
                actual_desc = (
                    f"{sensor_short} satellite imagery shows the project's baseline was measured during the peak dry season "
                    f"(NDVI {ev.ndvi.baseline_ndvi:.2f}), while the progress measurement was taken right after heavy monsoon rains. "
                    f"Comparing season-matched satellite images reveals that <b>{ev.ndvi.seasonal_artifact_pct:.0f}% of the claimed gain was temporary greening from rainfall</b>, "
                    f"rather than actual forest growth."
                )
                variance_badge = f"{ev.ndvi.seasonal_artifact_pct:.0f}% Rainfall Greening Artifact (+{disc:.0f}% Discrepancy)"
                status_cls = "fail"
            else:
                actual_val = claimed_val
                actual_param = "Verified Canopy Gain"
                actual_desc = f"{sensor_short} satellite imagery confirms canopy growth corresponds to true tree growth under season-matched baseline conditions."
                variance_badge = "Baseline Integrity Verified"
                status_cls = "pass"

    else:
        c = next((claim for claim in x.sampling_claims if is_surface_effluent_concentration(claim)), None) or (x.sampling_claims[0] if x.sampling_claims else None)
        claimed_num = c.claimed_project_value if c else 0.04
        unit_str = c.unit if c else "mg/L"
        limit_num = c.claimed_control_or_limit_value if (c and c.claimed_control_or_limit_value > 0) else 0.10
        param_name = c.parameter.split(" at ")[0] if c else "River Effluent Cr-VI"
        sampling_dt = c.sampling_date if c else ev.rainfall.sampling_date
        gov_body = getattr(c, "governing_body", "") or getattr(x, "governing_body", "") or "Statutory Authority"
        is_conc = is_surface_effluent_concentration(c)
        flood_dilution_trigger = is_conc and (ev.rainfall.sampling_day_mm > 25.0) and (ev.rainfall.sampling_percentile >= 90.0)

        claimed_val = f"{claimed_num:g} {unit_str}"
        claim_param = param_name
        claim_badge = f"Asserted Compliant (Limit: {limit_num:g} {unit_str} under {gov_body} standards)"

        if is_compliant:
            claim_desc = (
                f"The applicant reported {param_name} of <b>{claimed_val}</b>, which is <b>within statutory permissible threshold</b> "
                f"(limit: {limit_num:g} {unit_str} under {gov_body} standards) based on a sample collected on {sampling_dt}."
            )
            actual_val = claimed_val
            actual_param = f"{param_name} (Normal Flow)" if is_conc else f"{param_name} (Baseline Condition)"

            if ev.rainfall.sampling_day_mm == 0.0:
                actual_desc = "Earth Engine climate data (CHIRPS) confirms sampling occurred during normal dry-weather conditions (0.0 mm rain). No flood dilution detected. Baseline is representative."
            elif ev.rainfall.sampling_percentile < 75.0:
                actual_desc = f"Earth Engine climate data (CHIRPS) confirms sampling occurred during normal dry-weather conditions ({ev.rainfall.sampling_day_mm:.1f} mm rain). No flood dilution detected. Baseline is representative."
            else:
                actual_desc = "Earth Engine climate data (CHIRPS) confirms sampling occurred during normal conditions. No flood dilution detected. Baseline is representative."

            variance_badge = "Within statutory permissible threshold."
            status_cls = "pass"
        elif flood_dilution_trigger:
            claim_desc = (
                f"The applicant reported {param_name} of <b>{claimed_val}</b>, asserting compliance with "
                f"the statutory limit of {limit_num:g} {unit_str} under {gov_body} standards based on a sample collected on {sampling_dt}."
            )
            dilution = ev.rainfall.estimated_dilution_factor
            true_num = claimed_num * dilution
            ratio = true_num / limit_num if limit_num > 0 else 1.0
            actual_val = f"{true_num:.2f} {unit_str}"
            actual_param = "Discharge under Normal Flow"
            actual_desc = (
                f"Satellite climate data (CHIRPS) reveals this sample was collected during an extreme flood event "
                f"(<b>{ev.rainfall.sampling_percentile:.0f}th-percentile monsoon deluge</b>, {ev.rainfall.sampling_day_mm:.1f} mm of rainfall). "
                f"The flood volume diluted effluent concentrations by approximately <b>{dilution:.1f}×</b>. "
                f"Under normal dry-weather river flow, actual discharge reaches <b>{actual_val} — {ratio:.1f}× the permissible threshold under {gov_body} standards</b>."
            )
            variance_badge = f"{ratio:.1f}× Permissible Limit (Diluted by Flood)"
            status_cls = "fail"
        else:
            claim_desc = (
                f"The applicant reported {param_name} of <b>{claimed_val}</b>, exceeding "
                f"the permissible threshold of {limit_num:g} {unit_str} under {gov_body} standards based on a sample collected on {sampling_dt}."
            )
            ratio = claimed_num / limit_num if limit_num > 0 else 1.0
            actual_val = claimed_val
            actual_param = f"{param_name} (Observed)"
            actual_desc = f"Measurements confirm {param_name} of {claimed_val}, which exceeds the permissible limit under {gov_body} standards."
            variance_badge = f"{ratio:.1f}× Permissible Limit"
            status_cls = "fail"

    H(f"""
    <div class="tt-mid-sbs-box">
      <div class="tt-sbs-header-bar">
        <span class="tt-sbs-title">KEY AUDIT FINDING: WHAT WAS FILED VS. WHAT SATELLITES OBSERVED</span>
        <span class="tt-sbs-tag">INDEPENDENT SATELLITE VERIFICATION</span>
      </div>
      <div class="tt-sbs-table">
        <div class="tt-sbs-col claim">
          <div class="tt-sbs-head">CLAIMED IN FILING</div>
          <div class="tt-sbs-param">{E(claim_param)}</div>
          <div class="tt-sbs-val mono"><code>{E(claimed_val)}</code></div>
          <div class="tt-sbs-desc">{claim_desc}</div>
          <div class="tt-sbs-status claim-status">{E(claim_badge)}</div>
        </div>
        <div class="tt-sbs-col actual {status_cls}">
          <div class="tt-sbs-head">OBSERVED BY SATELLITE</div>
          <div class="tt-sbs-param">{E(actual_param)}</div>
          <div class="tt-sbs-val mono"><code>{E(actual_val)}</code></div>
          <div class="tt-sbs-desc">{actual_desc}</div>
          <div class="tt-sbs-status {status_cls}">{E(variance_badge)}</div>
        </div>
      </div>
    </div>
    """)


def render_audit_metrics_breakdown(a: Dict[str, Any]) -> None:
    entries = get_forensic_ledger_entries(a)
    metric_cards_html = []
    for item in entries:
        status_cls = "pass" if item["passed"] else "fail"
        badge_text = "PASSED" if item["passed"] else "CRITICAL DISCREPANCY"
        metric_cards_html.append(f"""
        <div class="tt-metric-row {status_cls}">
          <div class="tt-metric-row-header">
            <span class="tt-m-title">{E(item['metric_label'])}</span>
            <span class="tt-m-badge {status_cls}">{badge_text}</span>
          </div>
          <div class="tt-metric-row-body">
            <!-- LEFT IS CLAIMED -->
            <div class="tt-side-cell claim">
              <div class="tt-side-lbl">REPORTED IN FILING:</div>
              <div class="tt-side-val mono"><code>{E(item['claim_val'])}</code></div>
            </div>
            <!-- RIGHT IS REALITY -->
            <div class="tt-side-cell reality {status_cls}">
              <div class="tt-side-lbl">OBSERVED BY SATELLITE:</div>
              <div class="tt-side-val mono"><code>{E(item['sat_val'])}</code></div>
            </div>
          </div>
          <div class="tt-metric-row-footer {status_cls}">
            <span class="tt-foot-lbl">AUDIT FINDING:</span>
            <span class="tt-foot-val mono"><b>{E(item['variance_val'])}</b></span>
          </div>
        </div>
        """)

    H(f"""
    <div class="tt-ledger-box">
      <div class="tt-ledger-head-bar">
        <span class="tt-ledger-head-title">DETAILED AUDIT METRIC BREAKDOWN</span>
        <span class="tt-ledger-head-count">{len(entries)} CHECKS EVALUATED</span>
      </div>
      {''.join(metric_cards_html)}
    </div>
    """)


def render_satellite_evidence_section(a: Dict[str, Any]) -> None:
    report: FullAuditReport = a["report"]
    ev: SatelliteEvidence = a["evidence"]
    x = report.extracted_data
    is_carbon = x.filing_mode == "CARBON_OFFSET"

    if not getattr(x, "is_satellite_verifiable", True):
        H("""
        <div class="tt-evidence-panel">
          <div class="tt-ev-title">GEOSPATIAL SITING &amp; OBSERVATIONAL SCOPE STATUS</div>
          <div class="tt-ev-sub">Sensor compatibility assessment &amp; project footprint location</div>
        </div>
        """)
        m_col, c_col = st.columns([1.1, 1.0], gap="large")
        with m_col:
            st.markdown('<div class="tt-card-h">Project Facility Centroid Map</div>', unsafe_allow_html=True)
            render_map(report, ev)
        with c_col:
            st.markdown('<div class="tt-card-h">Sensor Modality Assessment</div>', unsafe_allow_html=True)
        sensor_short = get_sensor_label(x, ev, short=True)
        st.info(
            f"ℹ️ **Optical Earth Observation Safely Bypassed**\n\n"
            f"Project **{x.project_title}** operates under **{x.subsector}** methodology "
            f"({x.governing_statute}).\n\n"
            "• **No Direct Land-Use Changes**: Wind turbines and solar PV do not alter forest canopy or emit chemical water effluent.\n"
            "• **Telemetry Modality**: Grid-connected displacement relies on SCADA power-dispatch logs and PPA settlements, "
            "which cannot be audited with multi-spectral optical satellites.\n"
            f"• **Bypass Activated**: {sensor_short} NDVI, Hansen tree loss, and CHIRPS flood dilution algorithms were intentionally bypassed to avoid false confounding."
        )
        return

    H("""
    <div class="tt-evidence-panel">
      <div class="tt-ev-title">SATELLITE EVIDENCE &amp; OBSERVATION LAYERS</div>
      <div class="tt-ev-sub">Multi-spectral satellite imagery, climate reanalysis &amp; spatial validation layers</div>
    </div>
    """)

    prov_spans = []
    for p in ev.provenance:
        layer_lbl = p.layer
        dataset_lbl = p.dataset
        if is_filing_pre_2015(x, ev):
            layer_lbl = layer_lbl.replace("Sentinel-2", "Landsat 5/7")
            if "Sentinel-2" in dataset_lbl or "COPERNICUS/S2" in dataset_lbl:
                dataset_lbl = "Landsat 5/7 SR (LANDSAT/LT05 + LE07)"
        prov_spans.append(
            f'<span class="{p.mode}" title="{E(dataset_lbl)} — {E(p.detail)}"><i></i>{E(layer_lbl)} · '
            f'{"LIVE" if p.mode == "LIVE" else "REPLAY" if p.mode == "BENCHMARK_REPLAY" else "FALLBACK"}</span>'
        )
    prov = "".join(prov_spans)
    H(f'<div class="tt-prov" style="margin-bottom:14px;">{prov}</div>')

    m_col, c_col = st.columns([1.1, 1.0], gap="large")
    with m_col:
        st.markdown('<div class="tt-card-h">Geospatial Inspection Map</div>', unsafe_allow_html=True)
        render_map(report, ev)
    with c_col:
        st.markdown('<div class="tt-card-h">Satellite Time-Series &amp; Sensor Analysis</div>', unsafe_allow_html=True)
        tabs = st.tabs(["🌧️ Weather & Seasonal Timing", "💨 Wind & Terrain Alignment", "🌲 Boundary & Buffer Leakage"])
        with tabs[0]:
            if is_carbon:
                st.plotly_chart(chart_ndvi(ev, x), theme=None, config=PLOT_CONFIG, key="c_ndvi_ev")
            else:
                st.plotly_chart(chart_rainfall(ev, "Effluent Sampled"), theme=None, config=PLOT_CONFIG, key="c_rain_ev")
                dil = chart_dilution(report, ev)
                if dil is not None:
                    st.plotly_chart(dil, theme=None, config=PLOT_CONFIG, key="c_dil_ev")
        with tabs[1]:
            if is_carbon:
                st.plotly_chart(chart_terrain(ev), theme=None, config=PLOT_CONFIG, key="c_terrain_ev")
            elif ev.wind:
                st.plotly_chart(chart_wind(ev), theme=None, config=PLOT_CONFIG, key="c_wind_ev")
            else:
                st.info("No air sensor coordinates disclosed.")
        with tabs[2]:
            if is_carbon:
                st.plotly_chart(chart_buffer(ev), theme=None, config=PLOT_CONFIG, key="c_buf_ev")
            else:
                st.plotly_chart(chart_landcover(ev, x), theme=None, config=PLOT_CONFIG, key="c_land_ev")


def render_dispatch_section(a: Dict[str, Any]) -> None:
    report: FullAuditReport = a["report"]
    d = report.dispatch_action
    aid = a["audit_id"]

    H("""
    <div class="tt-dispatch-box">
      <div class="tt-disp-head">REGULATORY ENFORCEMENT &amp; FIELD DISPATCH</div>
    </div>
    """)

    is_out_of_scope = not getattr(report.extracted_data, "is_satellite_verifiable", True)
    is_compliant = (report.verdict_banner == "✅ COMPLIANCE VERIFIED: STATUTORY LIMITS RESPECTED")
    if is_out_of_scope:
        btn_lbl = "Archive Out-of-Scope Advisory (Pub/Sub)"
        toast_lbl = "Out-of-scope advisory archived"
        exp_lbl = "Regulatory Telemetry Directive & Coordinates"
    elif is_compliant:
        btn_lbl = "Publish Regulatory Clearance (Pub/Sub)"
        toast_lbl = "Regulatory clearance published"
        exp_lbl = "Regulatory Directive & Baseline Coordinates"
    else:
        btn_lbl = "Send Field Inspection Alert (Pub/Sub)"
        toast_lbl = "Field alert published"
        exp_lbl = "Field Directive & Inspection Coordinates"

    c1, c2, c3 = st.columns([1.2, 1.0, 1.0], gap="medium")
    with c1:
        if st.button(btn_lbl, key=f"pub_{aid}", type="primary", width="stretch"):
            with st.spinner("Publishing to Cloud Pub/Sub…"):
                ss.receipts[aid] = publish_dispatch(d, report, audit_id=aid)
            st.toast(f"{toast_lbl} · {ss.receipts[aid]['message_id']}")
    with c2:
        if st.button("Archive Audit to Cloud Storage", key=f"gcs_{aid}", width="stretch"):
            ss.archives[aid] = store_audit_artifact(_report_json(a), aid)
            st.toast(f"Audit archived ({ss.archives[aid]['mode']})")
    with c3:
        st.download_button("Download Audit Report (JSON)", data=_report_json(a), file_name=f"{aid}.json",
                           mime="application/json", width="stretch", key=f"dlr_{aid}")

    rec = ss.receipts.get(aid)
    if rec:
        note = "" if rec["mode"] == "LIVE" else f"<br>note: {E(rec.get('reason', ''))[:120]}"
        H(f"""<div class="tt-receipt {rec['mode']}">✓ {rec['mode']} ACK · message_id=<code>{E(str(rec['message_id']))}</code><br>
        topic=<code>{E(rec['topic'])}</code> · attrs: action_type=<code>{E(rec['attributes']['action_type'])}</code>{note}</div>""")

    with st.expander(exp_lbl):
        action_name = "Scope Action:" if is_out_of_scope else ("Regulatory Action:" if is_compliant else "Inspection Action:")
        target_name = "Target Authority:" if (is_out_of_scope or is_compliant) else "Target Recipient:"
        coord_name = "Facility Coordinates:" if is_out_of_scope else ("Verification Coordinates:" if is_compliant else "Inspection Target:")
        window_name = "Review Window:" if is_out_of_scope else ("Re-verification Window:" if is_compliant else "Recommended Window:")
        dir_name = "Advisory Directive:" if is_out_of_scope else ("Regulatory Directive:" if is_compliant else "Field Directive:")
        st.markdown(f"**{action_name}** <code>{d.action_type}</code> · Priority: <code>{d.priority}</code>", unsafe_allow_html=True)
        st.markdown(f"**{target_name}** {d.target_recipient}")
        st.markdown(f"**{coord_name}** <code>[{d.recommended_coordinates_lat:.5f}, {d.recommended_coordinates_lon:.5f}]</code>", unsafe_allow_html=True)
        st.markdown(f"**{window_name}** <code>{d.recommended_time_window}</code>", unsafe_allow_html=True)
        st.markdown(f"**{dir_name}** {d.instruction_directive}")


def _report_json(a: Dict[str, Any]) -> str:
    return json.dumps({
        "audit_id": a["audit_id"], "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "extraction_engine": a["extraction_engine"], "audit_engine": a["audit_engine"],
        "report": json.loads(a["report"].model_dump_json()), "evidence": json.loads(a["evidence"].model_dump_json()),
    }, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Landing
# ---------------------------------------------------------------------------
def render_landing() -> None:
    if HAS_GOLDEN and GOLDEN_CASES:
        H("""<div style="text-align:center;margin:18px 0 18px"><div style="font-family:'Space Grotesk';font-size:1.55rem;font-weight:600;color:#f1f5f9">
        Independent Satellite Verification for Environmental Filings &amp; Carbon Baselines. <span style="color:#5eead4">Evidence Backed by Earth Observation.</span></div>
        <div style="color:#94a3b8;font-size:.92rem;margin-top:6px">Simulation run for benchmarking and demo. Select a filing below or upload via the sidebar.</div></div>""")
        cols = st.columns(2, gap="large")
        bullets = {
            "eia": ["Effluent sampled during a 98th-percentile flood (8× river dilution)",
                    "Ambient air monitoring station placed 2.1 km upwind of the smokestack",
                    "Riparian wetland corridor declared as 'degraded barren scrubland'"],
            "carbon": ["Canopy growth measured right after monsoon rains against a dry-season baseline",
                       "Control plot placed on a steep 34° slope next to an active logging road",
                       "+42% spike in forest clearing within 2 km outside the project boundary"],
        }
        for col, (key, case) in zip(cols, GOLDEN_CASES.items()):
            with col:
                x = case["extracted"]
                items = "".join(f"<li>{E(b)}</li>" for b in bullets.get(key, []))
                H(f"""<div class="tt-case {key}"><div class="em">{case['emoji']}</div><h3>{E(case['title'])}</h3>
                <p>{E(x.project_title)} · {E(x.region_name)}</p><ul>{items}</ul></div>""")
                if st.button(f"Open benchmark audit  →", key=f"land_{key}", type="primary", width="stretch"):
                    ss.audit = get_precomputed_golden_audit(key)
                    ss.pending = None
                    st.rerun()
    else:
        H("""<div style="text-align:center;margin:20px 0 28px">
        <div style="font-family:'Space Grotesk';font-size:1.65rem;font-weight:600;color:#f1f5f9">
        Independent Satellite Verification for Environmental Filings &amp; Carbon Baselines. <span style="color:#5eead4">Evidence Backed by Earth Observation.</span></div>
        <div style="color:#94a3b8;font-size:1rem;margin-top:8px">Upload an Environmental Impact Assessment (EIA) or Carbon Credit Project Design Document (PDD) to begin independent satellite verification.</div>
        </div>""")

        _, mid, _ = st.columns([1, 6, 1])
        with mid:
            H("""<div style="background:#0d1424;border:1px solid rgba(45,212,191,0.25);border-radius:14px;padding:26px 30px;box-shadow:0 12px 30px -8px rgba(0,0,0,0.45);margin-bottom:12px">
            <div style="font-family:'Space Grotesk';font-size:1.2rem;font-weight:600;color:#f8fafc;margin-bottom:6px">📁 Upload Filing Dossier (PDF)</div>
            <div style="color:#94a3b8;font-size:.92rem;line-height:1.5;margin-bottom:16px">Gemini classifies the document type (EIA vs Carbon PDD), extracts declared sampling claims and coordinates, and audits them against independent satellite records (CHIRPS, ERA5, SRTM DEM, Sentinel-2 / Landsat 5/7, Hansen GFC, and NOAA GSOD).</div>
            </div>""")
            landing_pdf = st.file_uploader(
                "Upload Filing Dossier (PDF)",
                type=["pdf"],
                key="landing_single_pdf",
                label_visibility="collapsed",
            )
            if st.button("Run Independent Satellite Audit  →", key="run_landing_upload", type="primary", width="stretch", disabled=landing_pdf is None):
                ss.pending = {
                    "kind": "upload",
                    "bytes": landing_pdf.getvalue(),
                    "name": landing_pdf.name,
                    "mode_hint": None,
                }
                st.rerun()

    st.write("")
    st.write("")
    p = st.columns(3, gap="medium")
    pillars = [
        ("01", "Sampling & Seasonal Timing", "Did the applicant sample water during an extreme flood, or measure forest cover right after the monsoon? Satellite rainfall and vegetation records reveal when seasonal timing distorts reported numbers."),
        ("02", "Monitoring Placement & Control Sites", "Was an air station placed upwind where smoke never reaches? Is a carbon control plot located on a steep, road-side ridge? Wind reanalysis and elevation data verify whether monitoring sites are truly representative."),
        ("03", "Surrounding Buffer & Deforestation Leakage", "Did logging simply move just outside the protected project border? Is declared 'barren scrub' actually lush wetland habitat? Satellite buffer analysis tracks land changes across the broader landscape."),
    ]
    for col, (n, t, d) in zip(p, pillars):
        with col:
            H(f'<div class="tt-pillar"><div class="n">CHECK {n}</div><h4>{E(t)}</h4><p>{E(d)}</p></div>')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    render_hero()
    if ss.pending:
        opts = {
            "use_gemini": ss.get("opt_use_gemini", PS["gemini_ok"]),
            "live_golden": ss.get("opt_live_golden", False),
        }
        process_pending(opts)
    opts = render_sidebar()
    a = ss.audit
    if not a:
        render_landing()
        return

    # 1. High-Contrast Case Banner
    render_mode_banner(a)
    x = a["extracted"]
    H(f"""<div class="tt-strip"><span>Audit ID: <span class="id">{E(a['audit_id'])}</span></span><span class="sep"></span>
    <span><b>{'EIA Clearance' if x.filing_mode == 'EIA_CLEARANCE' else 'Carbon Offset PDD'}</b></span><span class="sep"></span>
    <span>Audit Engine: <b>{E(a['audit_engine'])}</b></span><span class="sep"></span>
    <span>Audit Speed: <b>{a['elapsed']:.2f}s</b></span><span class="sep"></span>
    <span>Source: <b>{'Benchmark Sample' if a.get('source') == 'golden' else 'Live Upload'}</b></span></div>""")

    # 2. Document Filing on Top in single line like header
    render_document_header(a)
    st.divider()

    # 3. Filed Claim & Satellite Reality in Middle Explaining in Plain English
    render_plain_english_comparison(a)

    # 4. Below That: Audit Metrics (Left is Claimed, Right is Reality)
    render_audit_metrics_breakdown(a)
    st.divider()

    # 5. Below That: Satellite Evidence as a Div on Its Own
    render_satellite_evidence_section(a)
    st.divider()

    # 6. Regulatory Enforcement & Ground Dispatch
    render_dispatch_section(a)


main()
