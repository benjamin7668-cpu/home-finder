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
    recent6, first3, last3 = set(yms[-6:]), set(yms[:3]), set(yms[-3:])

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

        start = _median([x.price_eok for x in items if x.ym in first3])
        end = _median([x.price_eok for x in items if x.ym in last3])
        change = round((end - start) / start * 100, 1) if start and end else None

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


def normalize(values: list[float], higher_is_better: bool) -> list[float]:
    lo, hi = min(values), max(values)
    if hi == lo:
        return [100.0] * len(values)
    return [((v - lo) if higher_is_better else (hi - v)) / (hi - lo) * 100 for v in values]


def _commute_dict(c: Commute) -> dict:
    return {"min": c.minutes, "transfers": c.transfers, "summary": c.summary, "walk": c.walk}


def score(cands: list[Candidate], budget_eok: float, weights: dict | None = None) -> list[dict]:
    if not cands:
        return []
    w = weights or DEFAULT_WEIGHTS
    commute_s = normalize([commute_metric(c.commute_a, c.commute_b) for c in cands], False)
    budget_s = normalize([(budget_eok - c.stat.median_6m) / budget_eok for c in cands], True)

    with_infra = [i for i, c in enumerate(cands) if c.infra is not None]
    infra_s = [0.0] * len(cands)
    if with_infra:
        for i, v in zip(with_infra, normalize([infra_raw(cands[i].infra) for i in with_infra], True)):
            infra_s[i] = v

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
