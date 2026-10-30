"""ODsay 대중교통 길찾기로 통근 시간 계산."""

from __future__ import annotations

from dataclasses import asdict

import httpx

from app.cache import Cache
from app.recommend import Commute, haversine_km

ENDPOINT = "https://api.odsay.com/v1/api/searchPubTransPathT"
COMMUTE_TTL = 30 * 86400
TOO_CLOSE_CODE = "-98"
WALK_KMH = 4.5


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


async def commute(
    client: httpx.AsyncClient, cache: Cache, key: str,
    origin: tuple[float, float], dest: tuple[float, float],
) -> Commute:
    cache_key = f"odsay:{origin[0]:.3f},{origin[1]:.3f}>{dest[0]:.3f},{dest[1]:.3f}"
    cached = cache.get(cache_key)
    if cached is not None:
        return Commute(**cached)

    try:
        resp = await client.get(
            ENDPOINT,
            params={"apiKey": key, "SX": origin[1], "SY": origin[0], "EX": dest[1], "EY": dest[0]},
            timeout=15,
        )
        resp.raise_for_status()
        body = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        raise CommuteUnavailable(f"ODsay 호출 실패: {e}") from e

    err = _error_code(body)
    if err and err[0] == TOO_CLOSE_CODE:
        minutes = round(haversine_km(origin, dest) / WALK_KMH * 60)
        result = Commute(minutes, 0, "도보", True)
    elif err:
        raise CommuteUnavailable(f"ODsay 오류 {err[0]}: {err[1]}")
    else:
        paths = body.get("result", {}).get("path") or []
        if not paths:
            raise CommuteUnavailable("ODsay 경로 없음")
        best = min(paths, key=lambda p: p["info"]["totalTime"])
        info = best["info"]
        transfers = max(info.get("busTransitCount", 0) + info.get("subwayTransitCount", 0) - 1, 0)
        result = Commute(int(info["totalTime"]), transfers, _summary(best), False)

    cache.set(cache_key, asdict(result), ttl=COMMUTE_TTL)
    return result
