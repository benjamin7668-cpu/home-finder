import pytest

from app.recommend import (
    Candidate,
    Commute,
    DongStat,
    Infra,
    aggregate,
    budget_filter,
    commute_metric,
    haversine_km,
    infra_raw,
    normalize,
    score,
    top_by_distance,
)
from app.sources.molit import Trade, recent_months
from datetime import date

MONTHS = recent_months(date(2026, 10, 30))  # 202511 ~ 202610
NAMES = {"11680": "서울특별시 강남구", "11620": "서울특별시 관악구"}


def t(dong, price, ym="2026-09", area=59.0, sgg="11680"):
    return Trade(sgg, dong, "아파트", area, price, ym, 1)


def stat(key="11680:A", median=5.0, deals=5):
    sgg, dong = key.split(":")
    return DongStat(key, sgg, "서울특별시 강남구", dong, median, deals, [], None)


def test_aggregate_median_and_band():
    trades = [t("역삼동", 8.0), t("역삼동", 9.0), t("역삼동", 10.0), t("역삼동", 20.0, area=84.0)]
    [s] = aggregate(trades, "59", MONTHS, NAMES)
    assert s.key == "11680:역삼동" and s.sgg_name == "서울특별시 강남구"
    assert s.median_6m == 9.0 and s.deals_6m == 3


def test_aggregate_only_last6_for_median():
    trades = [t("역삼동", 9.0), t("역삼동", 30.0, ym="2026-01")]
    [s] = aggregate(trades, "all", MONTHS, NAMES)
    assert s.median_6m == 9.0 and s.deals_6m == 1
    by_ym = {p["ym"]: p for p in s.trend}
    assert len(s.trend) == 12
    assert by_ym["2026-01"] == {"ym": "2026-01", "median": 30.0, "count": 1}
    assert by_ym["2026-02"] == {"ym": "2026-02", "median": None, "count": 0}


def test_aggregate_same_dong_name_different_sgg():
    stats = aggregate([t("신사동", 9.0), t("신사동", 5.0, sgg="11620")], "all", MONTHS, NAMES)
    assert {s.key for s in stats} == {"11680:신사동", "11620:신사동"}


def test_trend_change_pct():
    trades = [t("A", 8.0, ym="2025-11"), t("A", 8.4, ym="2026-10")]
    [s] = aggregate(trades, "all", MONTHS, NAMES)
    assert s.trend_change_pct == 5.0


def test_trend_change_none_when_window_empty():
    [s] = aggregate([t("A", 8.4, ym="2026-10")], "all", MONTHS, NAMES)
    assert s.trend_change_pct is None


def test_budget_filter_min_deals():
    r = budget_filter([stat("11680:A", 5.0, 5), stat("11680:B", 5.0, 2)], budget_eok=8.0)
    assert [s.key for s in r.kept] == ["11680:A"]
    assert r.low_deal_excluded == 1 and r.nearest_over_budget == []


def test_budget_filter_none_pass_returns_nearest():
    stats = [stat(f"11680:{m}", float(m)) for m in (8, 5, 7, 6)]
    r = budget_filter(stats, budget_eok=1.0)
    assert r.kept == []
    assert [s.median_6m for s in r.nearest_over_budget] == [5.0, 6.0, 7.0]


def test_haversine():
    assert haversine_km((37.5547, 126.9707), (37.4979, 127.0276)) == pytest.approx(8.0, abs=0.3)


def test_top_by_distance_orders_by_sum():
    a, b = (37.50, 127.00), (37.56, 126.90)
    near = (stat("11680:near"), (37.53, 126.95))
    mid = (stat("11680:mid"), (37.45, 127.05))
    far = (stat("11680:far"), (37.30, 127.30))
    out = top_by_distance([far, near, mid], a, b, n=2)
    assert [s.key for s, _ in out] == ["11680:near", "11680:mid"]


def test_commute_metric_fairness():
    assert commute_metric(Commute(40, 0, "", False), Commute(40, 0, "", False)) == 80
    assert commute_metric(Commute(20, 0, "", False), Commute(60, 0, "", False)) == 100


def test_infra_raw_caps():
    assert infra_raw(Infra(3, 5, 3, 20, 5)) == 9.0  # 모든 항목 상한 → 가중치 합
    assert infra_raw(Infra(99, 0, 0, 0, 0)) == 3.0


def test_normalize_all_equal():
    assert normalize([5, 5, 5], True) == [100, 100, 100]


def test_normalize_direction():
    assert normalize([1, 2, 3], False) == [100, 50, 0]
    assert normalize([1, 2, 3], True) == [0, 50, 100]


def _cand(key, a, b, infra, median):
    return Candidate(stat(key, median), (37.5, 127.0),
                     Commute(a, 1, "2호선", False), Commute(b, 0, "6호선", False), infra)


def test_score_weights_change_ranking():
    good_commute = _cand("11680:통근", 20, 20, Infra(0, 0, 0, 0, 0), 5.0)
    good_infra = _cand("11680:인프라", 50, 50, Infra(3, 5, 3, 20, 5), 5.0)
    default = score([good_commute, good_infra], budget_eok=8.0)
    assert default[0]["key"] == "11680:통근"
    infra_only = score([good_commute, good_infra], 8.0, {"commute": 0, "infra": 1, "budget": 0})
    assert infra_only[0]["key"] == "11680:인프라"


def test_score_result_shape():
    [r] = score([_cand("11680:역삼동", 30, 40, Infra(1, 2, 1, 5, 1), 6.0)], 8.0)
    assert r["name"] == "서울특별시 강남구 역삼동"
    assert r["commute"]["a"] == {"min": 30, "transfers": 1, "summary": "2호선", "walk": False}
    assert r["infra"] == {"subway": 1, "school": 2, "mart": 1, "hospital": 5, "park": 1}
    assert set(r["scores"]) == {"commute", "infra", "budget", "total"}
    assert all(isinstance(v, int) for v in r["scores"].values())


def test_score_infra_none_scores_zero_infra():
    with_infra = _cand("11680:A", 30, 30, Infra(1, 1, 1, 1, 1), 6.0)
    without = _cand("11680:B", 30, 30, None, 6.0)
    results = {r["key"]: r for r in score([with_infra, without], 8.0)}
    assert results["11680:B"]["infra"] is None
    assert results["11680:B"]["scores"]["infra"] == 0
