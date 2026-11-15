import logging
import os
from pathlib import Path
import threading
import uuid
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException

import provider

HOSTS = {
    "tiktok": {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"},
    "instagram": {"instagram.com", "www.instagram.com", "m.instagram.com"},
    "youtube": {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"},
    "facebook": {"facebook.com", "www.facebook.com", "m.facebook.com", "fb.watch"},
    "x": {"x.com", "www.x.com", "twitter.com", "www.twitter.com"},
}
# Only curated messages cross the public API boundary.
STATES = {
    "healthy": (200, "Layanan siap menerima permintaan."),
    "success": (200, "Permintaan berhasil dikonfirmasi oleh layanan. Penambahan metrik dapat memerlukan waktu."),
    "pending": (202, "Permintaan diterima dan sedang diproses. Jangan kirim ulang."),
    "failed": (422, "Permintaan ditolak. Periksa target atau coba layanan lain."),
    "rate_limited": (429, "Batas layanan tercapai. Tunggu sebelum mengirim permintaan berikutnya."),
    "busy": (429, "Server sedang memproses permintaan lain. Coba kembali setelah selesai."),
    "unavailable": (503, "Layanan sementara tidak tersedia. Coba kembali nanti."),
    "unknown": (502, "Hasil permintaan belum dapat dipastikan. Periksa target sebelum mengirim ulang."),
    "unsupported": (422, "Kombinasi platform dan layanan ini belum tersedia."),
    "invalid_request": (422, "Data tidak valid. Periksa url, platform, dan service."),
    "not_found": (404, "Endpoint tidak ditemukan."),
    "method_not_allowed": (405, "Metode HTTP tidak tersedia untuk endpoint ini."),
    "error": (500, "Terjadi kesalahan internal. Hubungi administrator dengan request_id."),
}
DOCS = os.getenv("ENABLE_DOCS", "false").lower() == "true"
app = FastAPI(title="Social API", version="2.3.0", debug=False,
    docs_url="/docs" if DOCS else None, redoc_url=None, openapi_url="/openapi.json" if DOCS else None)
claim_lock = threading.Lock()
logger = logging.getLogger("social_api")


class ClaimInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2048)
    platform: str = Field(min_length=1, max_length=30, pattern=r"^[a-z0-9_]+$")
    service: Literal["views", "likes", "followers"]

    @field_validator("url", "platform", "service", mode="before")
    @classmethod
    def normalize(cls, value, info):
        if isinstance(value, str):
            return value.strip() if info.field_name == "url" else value.strip().lower()
        return value


class Target(BaseModel):
    url: str | None = None
    platform: str | None = None
    service: str | None = None


class Envelope(BaseModel):
    message: str
    request_id: str
    data: Target


def envelope(request, state, retry=None, http_status=None):
    status, message = STATES[state]
    body = Envelope(message=message,
        request_id=getattr(request.state, "request_id", uuid.uuid4().hex),
        data=Target(**getattr(request.state, "target", {})))
    headers = {"X-Request-ID": body.request_id, "Cache-Control": "no-store"}
    if retry is not None:
        headers["Retry-After"] = str(retry)
    if state == "method_not_allowed":
        headers["Allow"] = "POST" if request.url.path == "/api/claim" else "GET"
    return JSONResponse(body.model_dump(), status_code=http_status or status, headers=headers)


@app.middleware("http")
async def boundary(request: Request, call_next):
    request.state.request_id = uuid.uuid4().hex
    try:
        response = await call_next(request)
    except Exception:
        # Exception text and tracebacks may contain credentials or upstream URLs.
        logger.error("request_failed request_id=%s", request.state.request_id)
        response = envelope(request, "error")
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.exception_handler(RequestValidationError)
async def validation_handler(request, exc):
    # Echo only the three input fields; never raw validation errors or extra fields.
    if isinstance(exc.body, dict):
        request.state.target = {
            key: value.strip() if key == "url" else value.strip().lower()
            for key in ("url", "platform", "service")
            if isinstance((value := exc.body.get(key)), str)
            and len(value) <= (2048 if key == "url" else 30)
        }
    return envelope(request, "invalid_request")


@app.exception_handler(HTTPException)
async def http_handler(request, exc):
    state = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "invalid_request")
    return envelope(request, state, http_status=exc.status_code)


def valid_url(url, platform):
    try:
        parsed = urlsplit(url)
        return (url.startswith("https://") and not any(ord(c) < 33 or ord(c) == 127 for c in url)
            and parsed.hostname in HOSTS[platform] and parsed.port in (None, 443)
            and not parsed.username and not parsed.password and bool(parsed.path.strip("/")))
    except (ValueError, KeyError):
        return False


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/health", include_in_schema=False)
def health(request: Request):
    return envelope(request, "healthy")


@app.post("/api/claim", response_model=Envelope,
    responses={202: {"model": Envelope}, 422: {"model": Envelope},
               429: {"model": Envelope}, 500: {"model": Envelope}, 502: {"model": Envelope}, 503: {"model": Envelope}})
def claim(data: ClaimInput, request: Request):
    request.state.target = data.model_dump()
    if data.platform not in HOSTS:
        return envelope(request, "unsupported")
    if not valid_url(data.url, data.platform):
        return envelope(request, "invalid_request")
    if (data.platform, data.service) not in provider.ROUTES:
        return envelope(request, "unsupported")
    if not claim_lock.acquire(blocking=False):
        return envelope(request, "busy")
    try:
        result = provider.execute(data.platform, data.service, data.url)
        return envelope(request, result.status, retry=result.retry_after)
    finally:
        claim_lock.release()
