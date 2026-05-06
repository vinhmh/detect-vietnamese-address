"""
main.py — FastAPI address resolution server
────────────────────────────────────────────
Start:  cd detect-vietnamese-address
        .venv/bin/uvicorn api.main:app --reload --port 8000

Endpoints:
  GET  /             → serves the web UI (index.html)
  POST /resolve      → resolve a raw address
  GET  /search       → quick fuzzy search (for autocomplete)
  GET  /health       → liveness check
"""

import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from api.resolver import MERGER_DATE, UnitMatch, _best_match as retrieve, resolve, to_ascii

# ── Config ────────────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent.parent / ".env")
DSN = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/vn_address")
UI_DIR = Path(__file__).parent / "ui"

# ── DB connection pool ────────────────────────────────────────────────────────

_conn: psycopg.Connection | None = None


def get_conn() -> psycopg.Connection:
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg.connect(DSN, row_factory=None)
    return _conn


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_conn()
    yield
    if _conn and not _conn.closed:
        _conn.close()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Vietnamese Address Resolver",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response models ─────────────────────────────────────────────────

class ResolveRequest(BaseModel):
    address: str
    as_of_date: str | None = None   # ISO date e.g. "2025-06-30" for pre-merger
    use_geocoder: bool = True        # set False to skip Nominatim/Google lookup


class UnitOut(BaseModel):
    id: int
    gso_code: str
    full_name: str
    name: str
    unit_type: str
    ascii_name: str
    score: float


class CandidateOut(BaseModel):
    score: float
    reasons: list[str]
    province: UnitOut | None = None
    district: UnitOut | None = None
    ward: UnitOut | None = None


def unit_to_out(u: UnitMatch | None) -> UnitOut | None:
    if u is None:
        return None
    return UnitOut(
        id=u.id, gso_code=u.gso_code, full_name=u.full_name,
        name=u.name, unit_type=u.unit_type, ascii_name=u.ascii_name,
        score=round(u.score, 4),
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT era, COUNT(*) as cnt
                FROM admin_units
                GROUP BY era
                ORDER BY era
            """)
            rows = cur.fetchall()
        counts = {row[0]: row[1] for row in rows}
        today = date.today()
        return {
            "status": "ok",
            "today": today.isoformat(),
            "active_era": "post_merger" if today >= MERGER_DATE else "pre_merger",
            "counts_by_era": counts,
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@app.post("/resolve")
def resolve_address(req: ResolveRequest) -> dict[str, Any]:
    if not req.address.strip():
        raise HTTPException(status_code=400, detail="address must not be empty")

    # Parse as_of_date — default to today (post-merger)
    as_of: date | None = None
    if req.as_of_date:
        try:
            as_of = date.fromisoformat(req.as_of_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="as_of_date must be ISO format YYYY-MM-DD")

    conn = get_conn()
    result = resolve(req.address, conn, as_of_date=as_of, use_geocoder=req.use_geocoder)

    candidates_out = []
    for c in result.candidates:
        candidates_out.append(CandidateOut(
            score=round(c.score, 3),
            reasons=c.reasons,
            province=unit_to_out(c.province),
            district=unit_to_out(c.district),
            ward=unit_to_out(c.ward),
        ).model_dump())

    top = result.top
    return {
        "raw_input":    result.raw_input,
        "normalized":   result.normalized,
        "confidence":   result.confidence,
        "action":       result.action,
        "era":              result.era,
        "as_of_date":       (as_of or date.today()).isoformat(),
        "house_number":     result.house_number,
        "street_raw":       result.street_raw,
        "geocoder_used":    result.geocoder_used,
        "geocoder_source":  result.geocoder_source,
        "geocoder_address": result.geocoder_address,
        "top": CandidateOut(
            score=round(top.score, 3),
            reasons=top.reasons,
            province=unit_to_out(top.province),
            district=unit_to_out(top.district),
            ward=unit_to_out(top.ward),
        ).model_dump() if top else None,
        "candidates": candidates_out,
    }


@app.get("/search")
def search(
    q: str = Query(..., min_length=1),
    type: str = Query("province", pattern="^(province|district|ward)$"),
    limit: int = Query(10, ge=1, le=30),
):
    """Fuzzy search admin units — useful for autocomplete."""
    conn = get_conn()
    ascii_q = to_ascii(q)
    hits = retrieve(conn, [ascii_q], unit_type=type, parent_id=None, threshold=0.05, top_k=limit)
    return [
        {
            "id":        h.id,
            "gso_code":  h.gso_code,
            "full_name": h.full_name,
            "name":      h.name,
            "unit_type": h.unit_type,
            "score":     round(h.score, 4),
        }
        for h in hits
    ]


# ── Serve the UI ──────────────────────────────────────────────────────────────

@app.get("/")
def serve_ui():
    ui_file = UI_DIR / "index.html"
    if not ui_file.exists():
        return JSONResponse({"message": "UI not found. Access /docs for the API."})
    return FileResponse(str(ui_file))
