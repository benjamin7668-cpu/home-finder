"""시연 전에 실거래가와 동네 좌표를 미리 캐시에 채운다: uv run python -m app.warmup"""

from __future__ import annotations

import asyncio
import sys
import time
from datetime import date

import httpx

from app.cache import Cache
from app.config import load_settings
from app.main import CACHE_PATH
from app.recommend import aggregate
from app.regions import capital_area_sggs
from app.sources import kakao, molit


async def main() -> int:
    s = load_settings()
    if not s.data_go_kr_key:
        print("DATA_GO_KR_API_KEY 없음: 실거래가를 받을 수 없습니다.")
        return 1
    cache, sggs, today, started = Cache(CACHE_PATH), capital_area_sggs(), date.today(), time.time()
    async with httpx.AsyncClient() as client:
        trades, failed = await molit.fetch_all(
            client, cache, s.data_go_kr_key, sggs, today,
            on_progress=lambda d, t: print(f"\r실거래가 {d}/{t}", end="", flush=True),
        )
        print(f"\n거래 {len(trades):,}건, 실패 시·군·구: {', '.join(failed) or '없음'}")
        if not s.kakao_rest_key:
            print("KAKAO_REST_API_KEY 없음: 동네 좌표는 건너뜁니다.")
        else:
            names = {g.code: g.name for g in sggs}
            stats = [x for x in aggregate(trades, "all", molit.recent_months(today), names) if x.deals_6m >= 3]
            sem = asyncio.Semaphore(10)

            async def one(stat):
                async with sem:
                    return await kakao.geocode(client, cache, s.kakao_rest_key, stat.name)

            found = await asyncio.gather(*(one(x) for x in stats), return_exceptions=True)
            ok = sum(1 for f in found if isinstance(f, list) and f)
            print(f"동네 좌표 {ok}/{len(stats)}")
    print(f"완료 ({time.time() - started:.0f}초)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
