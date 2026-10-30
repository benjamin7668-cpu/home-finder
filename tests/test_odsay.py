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
