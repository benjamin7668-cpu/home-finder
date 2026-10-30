"""파이프라인 테스트용 가짜 데이터 소스."""

from datetime import date

from app.config import Settings
from app.pipeline import Deps
from app.recommend import Commute, Infra
from app.regions import Sgg
from app.sources.kakao import Place
from app.sources.molit import Trade
from app.sources.odsay import CommuteUnavailable

TODAY = date(2026, 10, 30)
SETTINGS = Settings("D", "O", "KR", "KJ")
SGG = Sgg("11680", "서울특별시 강남구")
# 동 이름 → (좌표, 6개월 내 거래가 목록)
DONGS = {
    f"동{i}": ((37.50 + i * 0.01, 127.00 + i * 0.01), [5.0 + i, 5.0 + i, 5.0 + i])
    for i in range(5)
}


def make_deps(commute_minutes=None, fail=(), dongs=DONGS):
    commute_minutes = commute_minutes or {}

    async def fetch_all(client, cache, key, sggs, today, on_progress, months=12):
        on_progress(1, 1)
        trades = [Trade(SGG.code, d, "아파트", 59.0, p, "2026-09", 1)
                  for d, (_, prices) in dongs.items() for p in prices]
        return trades, []

    async def geocode(client, cache, key, query, limit=5):
        dong = query.split()[-1]
        if dong not in dongs:
            return []
        lat, lng = dongs[dong][0]
        return [Place(dong, query, lat, lng)]

    coord_to_dong = {v[0]: d for d, v in dongs.items()}

    async def commute(client, cache, key, origin, dest):
        dong = coord_to_dong[origin]
        if dong in fail:
            raise CommuteUnavailable("한도 초과")
        a, b = commute_minutes.get(dong, (30, 30))
        return Commute(a if dest == WORK_A else b, 0, "2호선", False)

    async def infra_counts(client, cache, key, dong_key, lat, lng):
        return Infra(1, 2, 1, 5, 1)

    return Deps(client=None, cache=None, settings=SETTINGS, today=TODAY, sggs=[SGG],
                fetch_all=fetch_all, geocode=geocode, commute=commute, infra_counts=infra_counts)


WORK_A = (37.4979, 127.0276)
WORK_B = (37.5790, 126.8900)
