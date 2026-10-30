"""동네 집계, 후보 거르기, 점수 계산. 외부 호출 없는 순수 함수만 둔다."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from statistics import median

from app.sources.molit import Trade, in_band

DEFAULT_WEIGHTS = {"commute": 0.5, "infra": 0.3, "budget": 0.2}
# (가중치, 상한)
INFRA_WEIGHTS = {"subway": (3, 3), "school": (2, 5), "mart": (2, 3), "hospital": (1, 20), "park": (1, 5)}
INFRA_MAX = sum(w for w, _ in INFRA_WEIGHTS.values())
# 점수는 후보끼리 비교하지 않고 절대 기준으로 매긴다 (후보가 적어도 꼴찌가 무조건 0점이 되지 않게)
COMMUTE_BEST, COMMUTE_WORST = 40, 140  # 통근 지표(분): 이하면 100점, 이상이면 0점
BUDGET_FULL_SLACK = 0.4  # 예산보다 40% 이상 싸면 예산 여유 100점
TREND_MIN_DEALS = 3  # 가격 변화율: 앞·뒤 6개월 각각 최소 거래 수


@dataclass(frozen=True)
class DongStat:
    key: str  # "sgg_code:동명"
    sgg_code: str
    sgg_name: str
    dong: str
    median_6m: float
    deals_6m: int
    trend: list[dict]
    trend_change_pct: float | None

    @property
    def name(self) -> str:
        return f"{self.sgg_name} {self.dong}"


@dataclass(frozen=True)
class BudgetResult:
    kept: list[DongStat]
    low_deal_excluded: int
    nearest_over_budget: list[DongStat]


@dataclass(frozen=True)
class Commute:
    minutes: int
    transfers: int
    summary: str
    walk: bool


@dataclass(frozen=True)
class Infra:
    subway: int
    school: int
    mart: int
    hospital: int
    park: int


@dataclass(frozen=True)
class Candidate:
    stat: DongStat
    coord: tuple[float, float]  # (lat, lng)
    commute_a: Commute
    commute_b: Commute
    infra: Infra | None


def _median(values: list[float]) -> float | None:
    return round(median(values), 2) if values else None


def aggregate(
    trades: list[Trade], band: str, months: list[str], sgg_names: dict[str, str]
) -> list[DongStat]:
    yms = [f"{m[:4]}-{m[4:]}" for m in months]
    recent6, first_half = set(yms[-6:]), set(yms[:6])

    by_dong: dict[tuple[str, str], list[Trade]] = defaultdict(list)
    for tr in trades:
        if tr.ym in yms and in_band(tr.area_m2, band):
            by_dong[(tr.sgg_code, tr.dong)].append(tr)

    stats = []
    for (sgg, dong), items in sorted(by_dong.items()):
        recent_prices = [x.price_eok for x in items if x.ym in recent6]
        if not recent_prices:
            continue
        by_month: dict[str, list[float]] = defaultdict(list)
        for x in items:
            by_month[x.ym].append(x.price_eok)
        trend = [{"ym": ym, "median": _median(by_month[ym]), "count": len(by_month[ym])} for ym in yms]

        # 앞 6개월 vs 뒤 6개월: 거래가 드문 동네도 변화율이 나오도록 넓게 묶되,
        # 몇 건뿐이면 단지 구성 차이가 변화율로 보이므로 각 기간 TREND_MIN_DEALS건 이상만 계산
        before = [x.price_eok for x in items if x.ym in first_half]
        after = [x.price_eok for x in items if x.ym in recent6]
        enough = len(before) >= TREND_MIN_DEALS and len(after) >= TREND_MIN_DEALS
        start, end = _median(before), _median(after)
        change = round((end - start) / start * 100, 1) if enough and start else None

        stats.append(DongStat(f"{sgg}:{dong}", sgg, sgg_names.get(sgg, sgg), dong,
                              _median(recent_prices), len(recent_prices), trend, change))
    return stats


def budget_filter(stats: list[DongStat], budget_eok: float, min_deals: int = 3) -> BudgetResult:
    enough = [s for s in stats if s.deals_6m >= min_deals]
    kept = [s for s in enough if s.median_6m <= budget_eok]
    nearest = [] if kept else sorted(enough, key=lambda s: s.median_6m)[:3]
    return BudgetResult(kept, len(stats) - len(enough), nearest)


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def top_by_distance(items, wa, wb, n: int = 30):
    return sorted(items, key=lambda it: haversine_km(it[1], wa) + haversine_km(it[1], wb))[:n]


def commute_metric(a: Commute, b: Commute) -> float:
    return a.minutes + b.minutes + 0.5 * abs(a.minutes - b.minutes)


def infra_raw(i: Infra) -> float:
    return float(sum(w * min(getattr(i, k), cap) / cap for k, (w, cap) in INFRA_WEIGHTS.items()))


def _clamp100(v: float) -> float:
    return max(0.0, min(100.0, v))


def commute_score(metric: float) -> float:
    return _clamp100((COMMUTE_WORST - metric) / (COMMUTE_WORST - COMMUTE_BEST) * 100)


def infra_score(i: Infra) -> float:
    return infra_raw(i) / INFRA_MAX * 100


def budget_score(budget_eok: float, median_eok: float) -> float:
    return _clamp100((budget_eok - median_eok) / budget_eok / BUDGET_FULL_SLACK * 100)


def _commute_dict(c: Commute) -> dict:
    return {"min": c.minutes, "transfers": c.transfers, "summary": c.summary, "walk": c.walk}


def score(cands: list[Candidate], budget_eok: float, weights: dict | None = None) -> list[dict]:
    if not cands:
        return []
    w = weights or DEFAULT_WEIGHTS
    commute_s = [commute_score(commute_metric(c.commute_a, c.commute_b)) for c in cands]
    budget_s = [budget_score(budget_eok, c.stat.median_6m) for c in cands]
    infra_s = [infra_score(c.infra) if c.infra is not None else 0.0 for c in cands]

    total_w = sum(w.values()) or 1
    results = []
    for c, cs, is_, bs in zip(cands, commute_s, infra_s, budget_s):
        total = (w["commute"] * cs + w["infra"] * is_ + w["budget"] * bs) / total_w
        results.append({
            "key": c.stat.key,
            "name": c.stat.name,
            "coord": {"lat": c.coord[0], "lng": c.coord[1]},
            "median_price": c.stat.median_6m,
            "deals_6m": c.stat.deals_6m,
            "commute": {"a": _commute_dict(c.commute_a), "b": _commute_dict(c.commute_b)},
            "infra": vars(c.infra) if c.infra else None,
            "trend": c.stat.trend,
            "trend_change_pct": c.stat.trend_change_pct,
            "scores": {"commute": round(cs), "infra": round(is_), "budget": round(bs), "total": round(total)},
        })
    return sorted(results, key=lambda r: r["scores"]["total"], reverse=True)
