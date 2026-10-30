import httpx

from app.cache import Cache
from app.recommend import Infra
from app.sources.kakao import Place, geocode, infra_counts


def _docs(*items):
    return {"documents": list(items), "meta": {"total_count": len(items)}}


def _addr(name, x, y):
    return {"address_name": name, "x": str(x), "y": str(y)}


def _place(name, x, y):
    return {"place_name": name, "address_name": f"{name} 주소", "road_address_name": "", "x": str(x), "y": str(y)}


async def test_geocode_address_first(tmp_path):
    paths = []

    def handler(req):
        paths.append(req.url.path)
        assert req.headers["Authorization"] == "KakaoAK K"
        return httpx.Response(200, json=_docs(_addr("서울 강남구 역삼동", 127.03, 37.50)))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        out = await geocode(c, Cache(tmp_path / "c.sqlite"), "K", "역삼동")
    assert paths == ["/v2/local/search/address.json"]
    assert out == [Place("서울 강남구 역삼동", "서울 강남구 역삼동", 37.50, 127.03)]


async def test_geocode_fallback_keyword(tmp_path):
    def handler(req):
        if req.url.path.endswith("address.json"):
            return httpx.Response(200, json=_docs())
        return httpx.Response(200, json=_docs(*[_place(f"강남역{i}", 127.0, 37.5) for i in range(3)]))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        out = await geocode(c, Cache(tmp_path / "c.sqlite"), "K", "강남역")
    assert [p.name for p in out] == ["강남역0", "강남역1", "강남역2"]


async def test_geocode_cached(tmp_path):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json=_docs(_addr("a", 1, 2)))

    cache = Cache(tmp_path / "c.sqlite")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        await geocode(c, cache, "K", "q")
        await geocode(c, cache, "K", "q")
    assert len(calls) == 1


async def test_geocode_empty_result_not_cached(tmp_path):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json=_docs())

    cache = Cache(tmp_path / "c.sqlite")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        assert await geocode(c, cache, "K", "없는곳") == []
        await geocode(c, cache, "K", "없는곳")
    assert len(calls) == 4  # 매번 address + keyword


async def test_infra_counts(tmp_path):
    totals = {"SW8": 2, "SC4": 5, "MT1": 1, "HP8": 12}
    seen = []

    def handler(req):
        p = req.url.params
        assert p["radius"] == "1000" and p["x"] == "127.0" and p["y"] == "37.5"
        if req.url.path.endswith("category.json"):
            seen.append(p["category_group_code"])
            n = totals[p["category_group_code"]]
        else:
            seen.append(p["query"])
            n = 3
        return httpx.Response(200, json={"documents": [], "meta": {"total_count": n}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        out = await infra_counts(c, Cache(tmp_path / "c.sqlite"), "K", "11680:역삼동", 37.5, 127.0)
    assert out == Infra(2, 5, 1, 12, 3)
    assert sorted(seen) == sorted(["SW8", "SC4", "MT1", "HP8", "공원"])
