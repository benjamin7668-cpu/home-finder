"""추천 처리 순서: 실거래 수집 → 예산 → 좌표 → 거리 → 통근 → 인프라 → 점수."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Literal

import httpx
from pydantic import BaseModel, Field

from app.cache import Cache
from app.config import Settings
from app.recommend import (
    Candidate,
    aggregate,
    budget_filter,
    commute_metric,
    score,
    top_by_distance,
)
from app.regions import Sgg, capital_area_sggs
from app.sources import kakao, molit, odsay

DISTANCE_TOP = 10  # ODsay 무료 한도(하루 30회) 안에서 새 직장 조합 1회 검색 = 최대 20회
INFRA_TOP = 15
RESULT_TOP = 10
CONCURRENCY = 10
REQUIRED_KEYS = {
    "DATA_GO_KR_API_KEY": ("data_go_kr_key", "실거래가 조회 불가"),
    "ODSAY_API_KEY": ("odsay_key", "통근 시간 계산 불가"),
    "KAKAO_REST_API_KEY": ("kakao_rest_key", "주소 검색·주변 시설 조회 불가"),
}

Emit = Callable[[str, dict], None]
# 한 동네의 외부 응답이 이상해도 그 동네만 빠지게 한다
PER_ITEM_ERRORS = (httpx.HTTPError, KeyError, TypeError, ValueError)


class PipelineError(Exception):
    pass


class RecommendRequest(BaseModel):
    budget_eok: float = Field(gt=0)
    band: Literal["59", "84", "all"]
    work_a: tuple[float, float]
    work_b: tuple[float, float]
    max_a: int = Field(60, gt=0)
    max_b: int = Field(60, gt=0)


@dataclass
class Deps:
    client: httpx.AsyncClient | None
    cache: Cache | None
    settings: Settings
    today: date
    sggs: list[Sgg] = field(default_factory=capital_area_sggs)
    fetch_all: Callable = molit.fetch_all
    geocode: Callable = kakao.geocode
    commute: Callable = odsay.commute
    infra_counts: Callable = kakao.infra_counts


def missing_key_message(settings: Settings) -> str | None:
    for env_name, (attr, what) in REQUIRED_KEYS.items():
        if not getattr(settings, attr):
            return f"{env_name} 없음: {what}"
    return None


async def _bounded(items: list, fn: Callable, emit: Emit, stage: str) -> list:
    semaphore = asyncio.Semaphore(CONCURRENCY)
    done = 0
    emit("progress", {"stage": stage, "done": 0, "total": len(items)})

    async def one(item):
        nonlocal done
        async with semaphore:
            result = await fn(item)
        done += 1
        emit("progress", {"stage": stage, "done": done, "total": len(items)})
        return result

    return await asyncio.gather(*(one(i) for i in items))


def _ym(yyyymm: str) -> str:
    return f"{yyyymm[:4]}-{yyyymm[4:]}"


async def run_recommend(req: RecommendRequest, deps: Deps, emit: Emit) -> dict[str, Any]:
    if msg := missing_key_message(deps.settings):
        raise PipelineError(msg)
    s = deps.settings
    months = molit.recent_months(deps.today)

    trades, failed_sggs = await deps.fetch_all(
        deps.client, deps.cache, s.data_go_kr_key, deps.sggs, deps.today,
        on_progress=lambda done, total: emit("progress", {"stage": "molit", "done": done, "total": total}),
    )
    if deps.sggs and len(failed_sggs) == len(deps.sggs):
        raise PipelineError("실거래가를 받지 못했습니다 (DATA_GO_KR_API_KEY와 공공데이터포털 활용신청을 확인하세요)")
    names = {g.code: g.name for g in deps.sggs}
    with_trades = {t.sgg_code for t in trades}
    empty_sggs = [g.name for g in deps.sggs if g.code not in with_trades and g.code not in failed_sggs]
    budget = budget_filter(aggregate(trades, req.band, months, names), req.budget_eok)

    async def locate(stat):
        try:
            places = await deps.geocode(deps.client, deps.cache, s.kakao_rest_key, stat.name)
        except PER_ITEM_ERRORS:
            return None
        return (stat, (places[0].lat, places[0].lng)) if places else None

    located = await _bounded(budget.kept, locate, emit, "geocode")
    with_coord = [x for x in located if x]
    nearby = top_by_distance(with_coord, req.work_a, req.work_b, DISTANCE_TOP)

    async def both_commutes(item):
        stat, coord = item
        try:
            a = await deps.commute(deps.client, deps.cache, s.odsay_key, coord, req.work_a)
            b = await deps.commute(deps.client, deps.cache, s.odsay_key, coord, req.work_b)
        except odsay.CommuteUnavailable:
            return None
        return stat, coord, a, b

    commuted = await _bounded(nearby, both_commutes, emit, "commute")
    unavailable = sum(1 for x in commuted if x is None)
    if nearby and unavailable == len(nearby):
        raise PipelineError("통근 시간을 확인할 수 있는 후보가 없습니다 (ODsay 키·한도를 확인하세요)")
    ok = [x for x in commuted if x]
    within = [x for x in ok if x[2].minutes <= req.max_a and x[3].minutes <= req.max_b]
    within.sort(key=lambda x: commute_metric(x[2], x[3]))

    async def with_infra(item):
        stat, coord, a, b = item
        try:
            infra = await deps.infra_counts(deps.client, deps.cache, s.kakao_rest_key, stat.key, *coord)
        except PER_ITEM_ERRORS:
            infra = None
        return Candidate(stat, coord, a, b, infra)

    candidates = await _bounded(within[:INFRA_TOP], with_infra, emit, "infra")

    return {
        "results": score(candidates, req.budget_eok)[:RESULT_TOP],
        "notes": {
            "failed_sggs": [names.get(c, c) for c in failed_sggs],
            "empty_sggs": empty_sggs,
            "low_deal_excluded": budget.low_deal_excluded,
            "geocode_failed": len(located) - len(with_coord),
            "commute_unavailable": unavailable,
            "over_max_commute": len(ok) - len(within),
            "nearest_over_budget": [
                {"name": n.name, "median_price": n.median_6m, "deals_6m": n.deals_6m}
                for n in budget.nearest_over_budget
            ],
        },
        "period": {"from": _ym(months[0]), "to": _ym(months[-1])},
    }
