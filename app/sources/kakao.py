"""카카오 로컬 API: 주소/장소 → 좌표, 반경 내 시설 개수."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass

import httpx

from app.cache import Cache
from app.recommend import Infra

BASE = "https://dapi.kakao.com/v2/local/"
INFRA_TTL = 30 * 86400
RADIUS_M = 1000
CATEGORIES = {"subway": "SW8", "school": "SC4", "mart": "MT1", "hospital": "HP8"}
PARK_QUERY = "공원"


@dataclass(frozen=True)
class Place:
    name: str
    address: str
    lat: float
    lng: float


async def _get(client: httpx.AsyncClient, key: str, path: str, params: dict) -> dict:
    resp = await client.get(BASE + path, params=params, headers={"Authorization": f"KakaoAK {key}"}, timeout=15)
    resp.raise_for_status()
    return resp.json()


async def geocode(client: httpx.AsyncClient, cache: Cache, key: str, query: str, limit: int = 5) -> list[Place]:
    cache_key = f"kakao:geo:{query}"
    cached = cache.get(cache_key)
    if cached is not None:
        return [Place(**p) for p in cached]

    data = await _get(client, key, "search/address.json", {"query": query, "size": limit})
    places = [Place(d["address_name"], d["address_name"], float(d["y"]), float(d["x"]))
              for d in data["documents"]]
    if not places:
        data = await _get(client, key, "search/keyword.json", {"query": query, "size": limit})
        places = [Place(d["place_name"], d.get("road_address_name") or d["address_name"],
                        float(d["y"]), float(d["x"])) for d in data["documents"]]

    places = places[:limit]
    if places:  # 빈 결과는 오타일 수 있으니 보관하지 않는다
        cache.set(cache_key, [asdict(p) for p in places], ttl=None)
    return places


async def infra_counts(
    client: httpx.AsyncClient, cache: Cache, key: str, dong_key: str, lat: float, lng: float
) -> Infra:
    cache_key = f"kakao:infra:{dong_key}"
    cached = cache.get(cache_key)
    if cached is not None:
        return Infra(**cached)

    near = {"x": lng, "y": lat, "radius": RADIUS_M, "size": 1}

    async def count_category(code: str) -> int:
        data = await _get(client, key, "search/category.json", {"category_group_code": code, **near})
        return int(data["meta"]["total_count"])

    async def count_park() -> int:
        data = await _get(client, key, "search/keyword.json", {"query": PARK_QUERY, **near})
        return int(data["meta"]["total_count"])

    counts = await asyncio.gather(*(count_category(c) for c in CATEGORIES.values()), count_park())
    infra = Infra(*counts)
    cache.set(cache_key, asdict(infra), ttl=INFRA_TTL)
    return infra
