import json
from pathlib import Path

import httpx
import pytest

from app.cache import Cache
from app.recommend import Commute
from app.sources.odsay import CommuteUnavailable, commute

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "odsay_sample.json").read_text(encoding="utf-8"))
SEOUL_STN = (37.5547, 126.9707)
GANGNAM = (37.4979, 127.0276)


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_commute_picks_min_time(tmp_path):
    def handler(req):
        p = req.url.params
        assert (p["SX"], p["SY"], p["EX"], p["EY"]) == ("126.9707", "37.5547", "127.0276", "37.4979")
        assert p["apiKey"] == "K+/="  # 특수문자 키도 그대로 전달
        return httpx.Response(200, json=SAMPLE)

    async with _client(handler) as c:
        out = await commute(c, Cache(tmp_path / "c.sqlite"), "K+/=", SEOUL_STN, GANGNAM)
    assert out == Commute(27, 0, "140", False)


async def test_commute_summary_and_transfers_for_subway_path(tmp_path):
    sample = {"result": {"path": [SAMPLE["result"]["path"][0]]}}
    async with _client(lambda r: httpx.Response(200, json=sample)) as c:
        out = await commute(c, Cache(tmp_path / "c.sqlite"), "K", SEOUL_STN, GANGNAM)
    assert out == Commute(31, 1, "수도권 1호선 → 수도권 2호선", False)


@pytest.mark.parametrize("body", [
    {"error": [{"code": "-98", "message": "출, 도착지가 700m이내입니다."}]},
    {"error": {"code": "-98", "msg": "출, 도착지가 700m이내입니다."}},
])
async def test_commute_walk_when_too_close(tmp_path, body):
    origin = (37.5000, 127.0000)
    dest = (37.5040, 127.0000)  # 약 0.445km
    async with _client(lambda r: httpx.Response(200, json=body)) as c:
        out = await commute(c, Cache(tmp_path / "c.sqlite"), "K", origin, dest)
    assert out == Commute(6, 0, "도보", True)


async def test_commute_other_error_raises(tmp_path):
    body = {"error": [{"code": "-8", "message": "필수 입력값 형식 및 범위 오류"}]}
    async with _client(lambda r: httpx.Response(200, json=body)) as c:
        with pytest.raises(CommuteUnavailable):
            await commute(c, Cache(tmp_path / "c.sqlite"), "K", SEOUL_STN, GANGNAM)


async def test_commute_http_error_raises(tmp_path):
    async with _client(lambda r: httpx.Response(500)) as c:
        with pytest.raises(CommuteUnavailable):
            await commute(c, Cache(tmp_path / "c.sqlite"), "K", SEOUL_STN, GANGNAM)


async def test_commute_failure_not_cached_success_cached(tmp_path):
    responses = [httpx.Response(500), httpx.Response(200, json=SAMPLE)]
    calls = []

    def handler(req):
        calls.append(1)
        return responses.pop(0) if responses else httpx.Response(500)

    cache = Cache(tmp_path / "c.sqlite")
    async with _client(handler) as c:
        with pytest.raises(CommuteUnavailable):
            await commute(c, cache, "K", SEOUL_STN, GANGNAM)
        assert (await commute(c, cache, "K", SEOUL_STN, GANGNAM)).minutes == 27
        assert (await commute(c, cache, "K", (37.55471, 126.97069), GANGNAM)).minutes == 27  # 소수 셋째 자리 같음
    assert len(calls) == 2


async def test_commute_unexpected_shape_raises_unavailable(tmp_path):
    body = {"result": {"path": [{"subPath": []}]}}  # info 없음
    async with _client(lambda r: httpx.Response(200, json=body)) as c:
        with pytest.raises(CommuteUnavailable):
            await commute(c, Cache(tmp_path / "c.sqlite"), "K", SEOUL_STN, GANGNAM)


async def test_commute_error_message_hides_key(tmp_path):
    async with _client(lambda r: httpx.Response(500)) as c:
        with pytest.raises(CommuteUnavailable) as e:
            await commute(c, Cache(tmp_path / "c.sqlite"), "SECRETKEY", SEOUL_STN, GANGNAM)
    assert "SECRETKEY" not in str(e.value)


async def test_commute_waits_out_429_then_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sources.odsay.RATE_LIMIT_WAIT", 0)
    responses = [httpx.Response(200, json={"error": {"code": "429", "message": "Too Many Requests"}}),
                 httpx.Response(200, json=SAMPLE)]
    async with _client(lambda r: responses.pop(0)) as c:
        out = await commute(c, Cache(tmp_path / "c.sqlite"), "K", SEOUL_STN, GANGNAM)
    assert out.minutes == 27


async def test_commute_429_gives_up_after_retries(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sources.odsay.RATE_LIMIT_WAIT", 0)
    calls = []

    def handler(r):
        calls.append(1)
        return httpx.Response(200, json={"error": {"code": "429", "message": "Too Many Requests"}})

    async with _client(handler) as c:
        with pytest.raises(CommuteUnavailable):
            await commute(c, Cache(tmp_path / "c.sqlite"), "K", SEOUL_STN, GANGNAM)
    assert len(calls) == 4  # 최초 + 재시도 3회


async def test_commute_requests_are_paced(tmp_path, monkeypatch):
    import asyncio
    import time
    from app.pacing import Pacer
    monkeypatch.setattr("app.sources.odsay.PACER", Pacer(0.05))
    starts = []

    def handler(r):
        starts.append(time.monotonic())
        return httpx.Response(200, json=SAMPLE)

    cache = Cache(tmp_path / "c.sqlite")
    async with _client(handler) as c:
        await asyncio.gather(*(commute(c, cache, "K", (37.5 + i * 0.01, 127.0), GANGNAM) for i in range(4)))
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert len(starts) == 4 and min(gaps) >= 0.045


async def test_commute_daily_quota_does_not_retry(tmp_path, monkeypatch):
    monkeypatch.setattr("app.sources.odsay.RATE_LIMIT_WAIT", 0)
    calls = []

    def handler(r):
        calls.append(1)
        return httpx.Response(200, json={"error": [{"code": "429", "message": "Daily quota exceeded"}]})

    async with _client(handler) as c:
        with pytest.raises(CommuteUnavailable, match="하루 호출 한도"):
            await commute(c, Cache(tmp_path / "c.sqlite"), "K", SEOUL_STN, GANGNAM)
    assert len(calls) == 1  # 하루 한도는 기다려도 안 풀리므로 재시도로 호출량을 낭비하지 않는다
