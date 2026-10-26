"""FastAPI app.

    uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import deps
from .routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    deps.pool()  # fail loudly at boot if Postgres is unreachable, not on first request
    yield
    deps.close_pool()


app = FastAPI(title="jobagent", version="1.0", lifespan=lifespan)

# Single-user. Loopback plus private-range LAN addresses, because `next dev`
# also serves on the machine's LAN IP and hitting that origin otherwise gets the
# response stripped of its CORS header. Ports 3000-3009: Next walks up the range
# when 3000 is taken. Tighten to an explicit origin list before this is ever
# exposed beyond a laptop.
_DEV_ORIGIN = (
    r"http://("
    r"localhost"
    r"|127\.0\.0\.1"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r"):300\d"
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=_DEV_ORIGIN,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
def health():
    with deps.conn() as c:
        c.execute("select 1")
    return {"ok": True}
