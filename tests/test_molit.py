from datetime import date
from pathlib import Path

import httpx
import pytest

from app.cache import Cache
from app.regions import Sgg
from app.sources.molit import (
    _to_eok,
    fetch_all,
    fetch_month,
    in_band,
    parse_trades,
    recent_months,
)

FIXTURE = (Path(__file__).parent / "fixtures" / "molit_sample.xml").read_text(encoding="utf-8")
TODAY = date(2026, 10, 30)


def test_to_eok():
    assert _to_eok("98,000") == 9.8
    assert _to_eok(" 125,500 ") == 12.55


def test_parse_trades_excludes_cancelled():
    ts = parse_trades(FIXTURE, "11680")
    assert len(ts) == 4  # 5건 중 1건 해제(cdealType=O)
    assert "대치삼성" not in {t.apt for t in ts}  # 픽스처에서 해제 처리한 거래
    first = ts[0]
    assert first.sgg_code == "11680"
    assert first.dong == "대치동"
    assert first.price_eok == 48.5
    assert first.area_m2 == 136.68
    assert first.ym == "2026-09"


def test_parse_trades_error_result_raises():
    xml = "<response><header><resultCode>30</resultCode><resultMsg>KEY ERROR</resultMsg></header></response>"
    with pytest.raises(RuntimeError):
        parse_trades(xml, "11680")


def test_in_band():
    assert in_band(59.9, "59") and in_band(50, "59") and in_band(65, "59")
    assert not in_band(66, "59") and not in_band(49.9, "59")
    assert in_band(84.97, "84") and not in_band(75.9, "84")
    assert in_band(200, "all")


def test_recent_months():
    assert recent_months(date(2026, 10, 30), 3) == ["202608", "202609", "202610"]
    assert recent_months(date(2026, 1, 5), 2) == ["202512", "202601"]
    assert len(recent_months(TODAY)) == 12


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_month_caches_past_month_forever(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url.params["DEAL_YMD"])
        return httpx.Response(200, content=FIXTURE.encode("utf-8"))

    cache = Cache(tmp_path / "c.sqlite")
    async with _client(handler) as client:
        a = await fetch_month(client, cache, "K", "11680", "202609", TODAY)
        b = await fetch_month(client, cache, "K", "11680", "202609", TODAY)
    assert calls == ["202609"]
    assert a == b and len(a) == 4
    row = cache._conn.execute("SELECT ttl FROM cache WHERE key='molit:11680:202609'").fetchone()
    assert row[0] is None


async def test_fetch_month_current_month_ttl_one_day(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    async with _client(lambda r: httpx.Response(200, content=FIXTURE.encode("utf-8"))) as client:
        await fetch_month(client, cache, "K", "11680", "202610", TODAY)
    row = cache._conn.execute("SELECT ttl FROM cache WHERE key='molit:11680:202610'").fetchone()
    assert row[0] == 86400


async def test_fetch_all_reports_failed_sgg(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sources.molit.RETRY_DELAYS", (0, 0))
    attempts = {"41135": 0}

    def handler(request):
        if request.url.params["LAWD_CD"] == "41135":
            attempts["41135"] += 1
            return httpx.Response(500)
        return httpx.Response(200, content=FIXTURE.encode("utf-8"))

    progress = []
    sggs = [Sgg("11680", "서울특별시 강남구"), Sgg("41135", "경기도 성남시 분당구")]
    async with _client(handler) as client:
        trades, failed = await fetch_all(
            client, Cache(tmp_path / "c.sqlite"), "K", sggs, TODAY,
            on_progress=lambda done, total: progress.append((done, total)), months=1,
        )
    assert failed == ["41135"]
    assert attempts["41135"] == 3  # 최초 1회 + 재시도 2회
    assert len(trades) == 4
    assert progress[-1] == (2, 2)


async def test_fetch_all_spaces_requests(tmp_path, monkeypatch):
    import time
    monkeypatch.setattr("app.sources.molit.MIN_INTERVAL", 0.05)
    starts = []

    def handler(request):
        starts.append(time.monotonic())
        return httpx.Response(200, content=FIXTURE.encode("utf-8"))

    sggs = [Sgg(f"1{i:04d}", "x") for i in range(6)]
    async with _client(handler) as client:
        await fetch_all(client, Cache(tmp_path / "c.sqlite"), "K", sggs, TODAY, lambda d, t: None, months=1)
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert len(starts) == 6 and min(gaps) >= 0.045  # 동시에 몰리지 않고 간격을 둔다


async def test_fetch_all_waits_out_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sources.molit.MIN_INTERVAL", 0)
    monkeypatch.setattr("app.sources.molit.RATE_LIMIT_WAIT", 0)
    monkeypatch.setattr("app.sources.molit.RETRY_DELAYS", ())  # 일반 재시도 없이도 429는 기다렸다 재시도
    responses = [httpx.Response(429), httpx.Response(200, content=FIXTURE.encode("utf-8"))]
    async with _client(lambda r: responses.pop(0)) as client:
        trades, failed = await fetch_all(
            client, Cache(tmp_path / "c.sqlite"), "K", [Sgg("11680", "x")], TODAY, lambda d, t: None, months=1)
    assert failed == [] and len(trades) == 4
