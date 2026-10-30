"""FastAPI 앱: 화면 제공과 추천 API."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import date
from typing import Callable

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.cache import Cache
from app.config import APP_ROOT, Settings, load_settings
from app.pipeline import Deps, PipelineError, RecommendRequest, run_recommend

STATIC = APP_ROOT / "app" / "static"
CACHE_PATH = APP_ROOT / "data" / "cache.sqlite"


def _default_deps(app: FastAPI) -> Deps:
    return Deps(client=app.state.client, cache=app.state.cache, settings=app.state.settings, today=date.today())


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def create_app(settings: Settings | None = None, make_deps: Callable[[FastAPI], Deps] = _default_deps) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings or load_settings()
        app.state.cache = Cache(CACHE_PATH)
        async with httpx.AsyncClient() as client:
            app.state.client = client
            yield

    app = FastAPI(title="우리 집 어디로?", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/config")
    async def config(request: Request):
        s: Settings = request.app.state.settings
        return {"kakao_js_key": s.kakao_js_key, "missing": s.missing()}

    @app.get("/api/geocode")
    async def geocode(request: Request, q: str):
        deps = make_deps(request.app)
        if not deps.settings.kakao_rest_key:
            raise HTTPException(503, "KAKAO_REST_API_KEY 없음: 주소 검색 불가")
        try:
            places = await deps.geocode(deps.client, deps.cache, deps.settings.kakao_rest_key, q.strip())
        except httpx.HTTPError as e:
            raise HTTPException(502, f"카카오 주소 검색 실패: {e}") from e
        return {"candidates": [vars(p) for p in places]}

    @app.post("/api/recommend")
    async def recommend(request: Request, body: RecommendRequest):
        deps = make_deps(request.app)
        queue: asyncio.Queue = asyncio.Queue()

        async def work():
            try:
                result = await run_recommend(body, deps, lambda name, data: queue.put_nowait((name, data)))
                queue.put_nowait(("result", result))
            except PipelineError as e:
                queue.put_nowait(("error", {"message": str(e)}))
            except Exception as e:  # 예기치 못한 오류도 화면에 알린다
                queue.put_nowait(("error", {"message": f"처리 중 오류: {e}"}))
            queue.put_nowait(None)

        async def stream():
            task = asyncio.create_task(work())
            try:
                while (item := await queue.get()) is not None:
                    yield _sse(*item)
            finally:
                task.cancel()

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


app = create_app()
