import pytest

from app.pipeline import PipelineError, RecommendRequest, run_recommend
from tests.fakes import WORK_A, WORK_B, make_deps


def req(**kw):
    base = dict(budget_eok=20.0, band="59", work_a=WORK_A, work_b=WORK_B)
    return RecommendRequest(**{**base, **kw})


async def run(r, deps):
    events = []
    out = await run_recommend(r, deps, lambda name, data: events.append((name, data)))
    return out, events


async def test_pipeline_happy_path():
    out, events = await run(req(), make_deps())
    totals = [r["scores"]["total"] for r in out["results"]]
    assert len(out["results"]) == 5 and totals == sorted(totals, reverse=True)
    assert {d["stage"] for n, d in events if n == "progress"} == {"molit", "geocode", "commute", "infra"}
    assert out["period"] == {"from": "2025-11", "to": "2026-10"}


async def test_pipeline_partial_commute_failure():
    out, _ = await run(req(), make_deps(fail={"동0", "동1"}))
    assert len(out["results"]) == 3
    assert out["notes"]["commute_unavailable"] == 2


async def test_pipeline_all_commute_fail_errors():
    with pytest.raises(PipelineError, match="통근 시간을 확인할 수 있는 후보가 없습니다"):
        await run(req(), make_deps(fail={f"동{i}" for i in range(5)}))


async def test_pipeline_max_commute_filter():
    out, _ = await run(req(max_a=30), make_deps(commute_minutes={"동2": (45, 20)}))
    assert "서울특별시 강남구 동2" not in {r["name"] for r in out["results"]}
    assert out["notes"]["over_max_commute"] == 1


async def test_pipeline_budget_none():
    out, _ = await run(req(budget_eok=1.0), make_deps())
    assert out["results"] == []
    assert [n["median_price"] for n in out["notes"]["nearest_over_budget"]] == [5.0, 6.0, 7.0]


async def test_pipeline_geocode_failure_skips_dong():
    deps = make_deps()
    original = deps.geocode

    async def geocode(client, cache, key, query, limit=5):
        return [] if query.endswith("동3") else await original(client, cache, key, query, limit)

    deps.geocode = geocode
    out, _ = await run(req(), deps)
    assert len(out["results"]) == 4 and out["notes"]["geocode_failed"] == 1


def test_request_validation():
    with pytest.raises(ValueError):
        req(budget_eok=0)
    with pytest.raises(ValueError):
        req(band="100")


async def test_pipeline_geocode_unexpected_shape_skips_dong():
    deps = make_deps()
    original = deps.geocode

    async def geocode(client, cache, key, query, limit=5):
        if query.endswith("동1"):
            raise KeyError("documents")
        return await original(client, cache, key, query, limit)

    deps.geocode = geocode
    out, _ = await run(req(), deps)
    assert len(out["results"]) == 4 and out["notes"]["geocode_failed"] == 1


async def test_pipeline_infra_unexpected_shape_keeps_candidate():
    deps = make_deps()

    async def infra_counts(*a, **kw):
        raise KeyError("meta")

    deps.infra_counts = infra_counts
    out, _ = await run(req(), deps)
    assert len(out["results"]) == 5 and all(r["infra"] is None for r in out["results"])


async def test_pipeline_all_sggs_failed_is_key_error_message():
    deps = make_deps()

    async def fetch_all(client, cache, key, sggs, today, on_progress, months=12):
        return [], [g.code for g in sggs]

    deps.fetch_all = fetch_all
    with pytest.raises(PipelineError, match="DATA_GO_KR_API_KEY"):
        await run(req(), deps)


async def test_pipeline_reports_districts_with_no_trades():
    from app.regions import Sgg
    deps = make_deps()
    deps.sggs = deps.sggs + [Sgg("28110", "인천광역시 중구")]
    out, _ = await run(req(), deps)
    assert out["notes"]["empty_sggs"] == ["인천광역시 중구"]
