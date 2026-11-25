import asyncio
import json
import os
import sqlite3
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Set

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

DB_PATH = os.getenv("DB_PATH", "./data/popcat.db")
MAX_POPS_PER_BATCH = max(1, int(os.getenv("MAX_POPS_PER_BATCH", "100")))
PPS_WINDOW_SECONDS = 5.0

BUILDINGS = [
    {"id": "bldg-1", "name": "ตึก 1", "shortName": "ตึก 1"},
    {"id": "bldg-2", "name": "ตึก 2", "shortName": "ตึก 2"},
    {"id": "bldg-3", "name": "ตึก 3", "shortName": "ตึก 3"},
    {"id": "bldg-4", "name": "ตึก 4", "shortName": "ตึก 4"},
    {"id": "bldg-8", "name": "ตึก 8", "shortName": "ตึก 8"},
    {"id": "bldg-9", "name": "ตึก 9", "shortName": "ตึก 9"},
    {"id": "bldg-50", "name": "ตึก 50 ปี", "shortName": "ตึก 50 ปี"},
    {"id": "bldg-55", "name": "ตึก 55 ปี", "shortName": "ตึก 55 ปี"},
    {"id": "bldg-60", "name": "ตึก 60 ปี", "shortName": "ตึก 60 ปี"},
    {"id": "bldg-khunying", "name": "ตึกคุณหญิงหรั่ง กันตารัติ", "shortName": "ตึกคุณหญิงหรั่งฯ"},
    {"id": "bldg-art", "name": "ตึกศิลปะ", "shortName": "ตึกศิลปะ"},
]
VALID_BUILDING_IDS = {b["id"] for b in BUILDINGS}

db_lock = threading.Lock()
pps_history: Dict[str, deque] = {b["id"]: deque() for b in BUILDINGS}
sse_subscribers: Set[asyncio.Queue] = set()
dirty_event: asyncio.Event | None = None


def get_db_connection() -> sqlite3.Connection:
    db_file = Path(DB_PATH)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_file), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def init_db() -> None:
    with db_lock:
        conn = get_db_connection()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS buildings (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    short_name TEXT NOT NULL,
                    score INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL DEFAULT 0
                )
                """
            )
            now = time.time()
            for b in BUILDINGS:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO buildings (id, name, short_name, score, updated_at)
                    VALUES (?, ?, ?, 0, ?)
                    """,
                    (b["id"], b["name"], b["shortName"], now),
                )
                conn.execute(
                    """
                    UPDATE buildings SET name = ?, short_name = ? WHERE id = ?
                    """,
                    (b["name"], b["shortName"], b["id"]),
                )
            conn.commit()
        finally:
            conn.close()


def compute_pps_locked(building_id: str, now: float) -> float:
    dq = pps_history[building_id]
    cutoff = now - PPS_WINDOW_SECONDS
    while dq and dq[0][0] < cutoff:
        dq.popleft()
    total_recent = sum(item[1] for item in dq)
    return round(total_recent / PPS_WINDOW_SECONDS, 1)


def get_snapshot() -> dict:
    now = time.time()
    with db_lock:
        conn = get_db_connection()
        try:
            rows = conn.execute("SELECT id, name, short_name, score FROM buildings").fetchall()
        finally:
            conn.close()

        row_map = {r["id"]: r for r in rows}
        buildings_payload: List[dict] = []
        total_pops = 0

        for b in BUILDINGS:
            bid = b["id"]
            score = int(row_map[bid]["score"]) if bid in row_map else 0
            pps = compute_pps_locked(bid, now)
            total_pops += score
            buildings_payload.append(
                {
                    "id": bid,
                    "name": b["name"],
                    "shortName": b["shortName"],
                    "score": score,
                    "pps": pps,
                }
            )

    buildings_payload.sort(key=lambda item: item["score"], reverse=True)
    return {
        "buildings": buildings_payload,
        "total_pops": total_pops,
        "server_time": now,
    }


def record_pops(building_id: str, count: int) -> None:
    now = time.time()
    with db_lock:
        conn = get_db_connection()
        try:
            conn.execute(
                """
                UPDATE buildings
                SET score = score + ?, updated_at = ?
                WHERE id = ?
                """,
                (count, now, building_id),
            )
            conn.commit()
        finally:
            conn.close()
        pps_history[building_id].append((now, count))


async def broadcaster_loop() -> None:
    global dirty_event
    dirty_event = asyncio.Event()
    while True:
        await dirty_event.wait()
        # Coalesce rapid bursts every 250ms before broadcasting
        await asyncio.sleep(0.25)
        dirty_event.clear()
        if not sse_subscribers:
            continue
        payload = json.dumps(get_snapshot(), ensure_ascii=False)
        stale: List[asyncio.Queue] = []
        for q in list(sse_subscribers):
            try:
                if q.full():
                    q.get_nowait()
                q.put_nowait(payload)
            except Exception:
                stale.append(q)
        for q in stale:
            sse_subscribers.discard(q)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(broadcaster_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="POPCAT เตรียมอุดมศึกษา", lifespan=lifespan)


class PopRequest(BaseModel):
    building_id: str = Field(..., min_length=1, max_length=64)
    count: int = Field(..., ge=1, le=10000)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/scores")
async def scores():
    return JSONResponse(get_snapshot())


@app.post("/api/pop")
async def pop(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    parsed = PopRequest(**body)
    if parsed.building_id not in VALID_BUILDING_IDS:
        raise HTTPException(status_code=400, detail="Unknown building_id")

    accepted_count = min(parsed.count, MAX_POPS_PER_BATCH)
    await asyncio.to_thread(record_pops, parsed.building_id, accepted_count)

    if dirty_event is not None:
        dirty_event.set()

    return {"ok": True, "accepted": accepted_count}


@app.get("/api/stream")
async def stream(request: Request):
    queue: asyncio.Queue = asyncio.Queue(maxsize=4)
    sse_subscribers.add(queue)

    async def event_generator():
        try:
            initial_payload = json.dumps(get_snapshot(), ensure_ascii=False)
            yield f"data: {initial_payload}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            sse_subscribers.discard(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


static_path = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(static_path / "index.html"))
