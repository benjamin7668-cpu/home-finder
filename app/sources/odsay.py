"""ODsay 대중교통 길찾기로 통근 시간 계산."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

import httpx

from app.cache import Cache
from app.pacing import Pacer
from app.recommend import Commute, haversine_km

ENDPOINT = "https://api.odsay.com/v1/api/searchPubTransPathT"
COMMUTE_TTL = 30 * 86400
TOO_CLOSE_CODE = "-98"
WALK_KMH = 4.5
# 동시에 몰리면 429(Too Many Requests). 0.25초 간격이면 통과(2026-10 실측).
PACER = Pacer(0.25)
RATE_LIMIT_CODE = "429"
RATE_LIMIT_WAIT = 2.0
RATE_LIMIT_RETRIES = 3


class CommuteUnavailable(Exception):
    pass


def _error_code(body: dict) -> tuple[str, str] | None:
    err = body.get("error")
    if not err:
        return None
    first = err[0] if isinstance(err, list) else err
    return str(first.get("code")), first.get("message") or first.get("msg") or ""


def _summary(path: dict) -> str:
    names = []
    for sub in path.get("subPath", []):
        if sub.get("trafficType") in (1, 2) and sub.get("lane"):
            lane = sub["lane"][0]
            names.append(lane.get("name") or lane.get("busNo") or "")
    return " → ".join(n for n in names if n)


async def _request(client: httpx.AsyncClient, key: str, origin, dest) -> dict:
    await PACER.wait()
    try:
        resp = await client.get(
            ENDPOINT,
            params={"apiKey": key, "SX": origin[1], "SY": origin[0], "EX": dest[1], "EY": dest[0]},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:  # 메시지에 apiKey가 든 URL을 남기지 않는다
        raise CommuteUnavailable(f"ODsay HTTP {e.response.status_code}") from None
    except (httpx.HTTPError, ValueError) as e:
        raise CommuteUnavailable(f"ODsay 호출 실패: {type(e).__name__}") from None


async def commute(
    client: httpx.AsyncClient, cache: Cache, key: str,
    origin: tuple[float, float], dest: tuple[float, float],
) -> Commute:
    cache_key = f"odsay:{origin[0]:.3f},{origin[1]:.3f}>{dest[0]:.3f},{dest[1]:.3f}"
    cached = cache.get(cache_key)
    if cached is not None:
        return Commute(**cached)

    for attempt in range(RATE_LIMIT_RETRIES + 1):
        body = await _request(client, key, origin, dest)
        err = _error_code(body)
        if not (err and err[0] == RATE_LIMIT_CODE):
            break
        if "daily" in err[1].lower():  # 하루 한도는 기다려도 풀리지 않는다
            raise CommuteUnavailable("ODsay 하루 호출 한도 초과")
        if attempt == RATE_LIMIT_RETRIES:
            raise CommuteUnavailable("ODsay 호출 한도 초과(429)")
        await asyncio.sleep(RATE_LIMIT_WAIT)

    if err and err[0] == TOO_CLOSE_CODE:
        minutes = round(haversine_km(origin, dest) / WALK_KMH * 60)
        result = Commute(minutes, 0, "도보", True)
    elif err:
        raise CommuteUnavailable(f"ODsay 오류 {err[0]}: {err[1]}")
    else:
        try:
            paths = body.get("result", {}).get("path") or []
            if not paths:
                raise CommuteUnavailable("ODsay 경로 없음")
            best = min(paths, key=lambda p: p["info"]["totalTime"])
            info = best["info"]
            transfers = max(info.get("busTransitCount", 0) + info.get("subwayTransitCount", 0) - 1, 0)
            result = Commute(int(info["totalTime"]), transfers, _summary(best), False)
        except (KeyError, TypeError, ValueError, AttributeError) as e:  # 예상과 다른 응답 형식
            raise CommuteUnavailable(f"ODsay 응답 형식 오류: {type(e).__name__}") from None

    cache.set(cache_key, asdict(result), ttl=COMMUTE_TTL)
    return result
