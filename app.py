import json
import logging
import os
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from yt_dlp import YoutubeDL

logger = logging.getLogger(__name__)

# Prefer a single progressive MP4 so the mobile client gets one playable URL.
_FORMAT_CHAIN = (
    "best[ext=mp4][acodec!=none][vcodec!=none]/"
    "best[ext=mp4]/"
    "22/18/"
    "best[acodec!=none][vcodec!=none][height<=1080]/"
    "best[acodec!=none][vcodec!=none]/"
    "best"
)

_YDL_BASE: dict[str, Any] = {
    "format": _FORMAT_CHAIN,
    "quiet": True,
    "no_warnings": True,
    "socket_timeout": 120,
    "noplaylist": True,
    "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
}


def _auth_config() -> tuple[str, str] | None:
    token = (
        os.environ.get("NEBULAE_API_TOKEN", "").strip()
        or os.environ.get("COBALT_AUTH_TOKEN", "").strip()
    )
    if not token:
        return None
    scheme = (
        os.environ.get("NEBULAE_API_AUTH_SCHEME", "").strip()
        or os.environ.get("COBALT_AUTH_SCHEME", "").strip()
        or "Bearer"
    )
    return scheme, token


def _check_auth(request: Request) -> bool:
    cfg = _auth_config()
    if cfg is None:
        return True
    scheme, token = cfg
    header = request.headers.get("authorization", "")
    expected = f"{scheme} {token}".strip()
    return header == expected


def _error_body(code: str) -> dict[str, Any]:
    return {"status": "error", "error": {"code": code}}


def _pick_direct_url(info: dict[str, Any]) -> str | None:
    url = info.get("url")
    if isinstance(url, str) and url.startswith("http"):
        return url
    for fmt in info.get("formats") or []:
        if not isinstance(fmt, dict):
            continue
        u = fmt.get("url")
        if isinstance(u, str) and u.startswith("http"):
            vcodec = (fmt.get("vcodec") or "none").lower()
            acodec = (fmt.get("acodec") or "none").lower()
            if vcodec != "none" and acodec != "none":
                return u
    return None


def resolve_media_url(page_url: str) -> str:
    opts = dict(_YDL_BASE)
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(page_url, download=False)
    if not isinstance(info, dict):
        raise ValueError("empty_extractor_result")
    picked = _pick_direct_url(info)
    if picked:
        return picked
    raise ValueError("no_direct_url")


def _looks_like_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


async def handle_health(_: Request) -> Response:
    return JSONResponse({"status": "ok"})


async def handle_resolve(request: Request) -> Response:
    if not _check_auth(request):
        return JSONResponse(_error_body("error.api.fetch.critical"), status_code=401)

    if request.method == "GET":
        return JSONResponse(
            {
                "service": "nebulae-ytdlp",
                "hint": "POST JSON {\"url\": \"https://...\"} (Cobalt-shaped).",
            }
        )

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse(_error_body("error.api.fetch.critical"), status_code=400)

    raw = body.get("url") if isinstance(body, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        return JSONResponse(_error_body("error.api.fetch.empty"), status_code=400)

    page_url = raw.strip()
    if not _looks_like_url(page_url):
        return JSONResponse(_error_body("error.api.fetch.critical"), status_code=400)

    try:
        stream_url = resolve_media_url(page_url)
    except Exception as exc:
        logger.warning("resolve_failed url=%s err=%s", page_url[:80], exc)
        return JSONResponse(
            _error_body("error.api.content.video.unavailable"), status_code=200
        )

    # Nebulae accepts status "tunnel" with any https media URL (no .mp4 path required).
    return JSONResponse({"status": "tunnel", "url": stream_url})


app = Starlette(
    routes=[
        Route("/health", endpoint=handle_health, methods=["GET"]),
        Route("/", endpoint=handle_resolve, methods=["GET", "POST"]),
    ]
)


def _install_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )


def main() -> None:
    _install_logging()
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
