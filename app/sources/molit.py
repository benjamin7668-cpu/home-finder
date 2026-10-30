"""국토교통부 아파트 매매 실거래가 조회."""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import date
from typing import Callable

import httpx

from app.cache import Cache
from app.pacing import Pacer
from app.regions import Sgg

ENDPOINT = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"
PAGE_SIZE = 1000
CURRENT_MONTH_TTL = 86400
RETRY_DELAYS = (0.5, 1.0)
CONCURRENCY = 10
# 공공데이터포털은 순간적으로 몰리면 429(초당 한도 초과)를 주고 약 40초간 막는다.
# 요청 시작 간격 0.2초(초당 5회 이하). 모듈 전역이라 여러 요청·탭이 함께 지킨다.
PACER = Pacer(0.2)
RATE_LIMIT_WAIT = 45
RATE_LIMIT_RETRIES = 3
BANDS = {"59": (50.0, 65.0), "84": (76.0, 90.0)}


class RateLimited(Exception):
    pass


@dataclass(frozen=True)
class Trade:
    sgg_code: str
    dong: str
    apt: str
    area_m2: float
    price_eok: float
    ym: str  # "YYYY-MM"
    day: int


def _to_eok(amount: str) -> float:
    return round(int(amount.strip().replace(",", "")) / 10000, 2)


def _text(item: ET.Element, tag: str) -> str:
    return (item.findtext(tag) or "").strip()


def _parse(xml: str) -> tuple[list[ET.Element], int]:
    root = ET.fromstring(xml)
    code = root.findtext("header/resultCode")
    if code != "000":
        raise RuntimeError(f"국토부 API 오류 {code}: {root.findtext('header/resultMsg')}")
    total = int(root.findtext("body/totalCount") or 0)
    return root.findall("body/items/item"), total


def _to_trades(items: list[ET.Element], sgg_code: str) -> list[Trade]:
    trades = []
    for item in items:
        if _text(item, "cdealType") == "O":  # 해제된 거래
            continue
        try:
            trades.append(_to_trade(item, sgg_code))
        except ValueError:  # 면적·금액 등이 비어 있는 행은 건너뛴다
            continue
    return trades


def parse_trades(xml: str, sgg_code: str) -> list[Trade]:
    items, _ = _parse(xml)
    return _to_trades(items, sgg_code)


def _to_trade(item: ET.Element, sgg_code: str) -> Trade:
    return Trade(
        sgg_code=sgg_code,
        dong=_text(item, "umdNm"),
        apt=_text(item, "aptNm"),
        area_m2=float(_text(item, "excluUseAr")),
        price_eok=_to_eok(_text(item, "dealAmount")),
        ym=f"{int(_text(item, 'dealYear')):04d}-{int(_text(item, 'dealMonth')):02d}",
        day=int(_text(item, "dealDay")),
    )


def in_band(area_m2: float, band: str) -> bool:
    if band == "all":
        return True
    low, high = BANDS[band]
    return low <= area_m2 <= high


def recent_months(today: date, n: int = 12) -> list[str]:
    months = []
    y, m = today.year, today.month
    for _ in range(n):
        months.append(f"{y:04d}{m:02d}")
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return months[::-1]


async def fetch_month(
    client: httpx.AsyncClient, cache: Cache, key: str, sgg_code: str, yyyymm: str, today: date,
    pacer: Pacer | None = None,
) -> list[Trade]:
    cache_key = f"molit:{sgg_code}:{yyyymm}"
    cached = cache.get(cache_key)
    if cached is not None:
        return [Trade(**t) for t in cached]

    trades: list[Trade] = []
    page = 1
    while True:
        await (pacer or PACER).wait()
        resp = await client.get(
            ENDPOINT,
            params={"serviceKey": key, "LAWD_CD": sgg_code, "DEAL_YMD": yyyymm,
                    "numOfRows": PAGE_SIZE, "pageNo": page},
            timeout=30,
        )
        if resp.status_code == 429:
            raise RateLimited(sgg_code)
        resp.raise_for_status()
        items, total = _parse(resp.content.decode("utf-8"))
        trades += _to_trades(items, sgg_code)
        if page * PAGE_SIZE >= total:
            break
        page += 1

    # 신고 기한(30일)과 해제 신고가 지나 확정된 달(두 달 전 이전)만 영구 보관한다
    settled = recent_months(today, 3)[0]
    ttl = None if yyyymm <= settled else CURRENT_MONTH_TTL
    cache.set(cache_key, [asdict(t) for t in trades], ttl=ttl)
    return trades


async def _fetch_sgg(client, cache, key, sgg_code, months, today, pacer) -> list[Trade]:
    out: list[Trade] = []
    for yyyymm in months:
        failures, limited = 0, 0
        while True:
            try:
                out += await fetch_month(client, cache, key, sgg_code, yyyymm, today, pacer)
                break
            except RateLimited:
                limited += 1
                if limited > RATE_LIMIT_RETRIES:
                    raise
                await asyncio.sleep(RATE_LIMIT_WAIT)
            except (httpx.HTTPError, RuntimeError, ET.ParseError, ValueError):
                if failures >= len(RETRY_DELAYS):
                    raise
                await asyncio.sleep(RETRY_DELAYS[failures])
                failures += 1
    return out


async def fetch_all(
    client: httpx.AsyncClient,
    cache: Cache,
    key: str,
    sggs: list[Sgg],
    today: date,
    on_progress: Callable[[int, int], None],
    months: int = 12,
) -> tuple[list[Trade], list[str]]:
    month_list = recent_months(today, months)
    semaphore = asyncio.Semaphore(CONCURRENCY)
    pacer = PACER
    trades: list[Trade] = []
    failed: list[str] = []
    done = 0

    async def one(sgg: Sgg) -> None:
        nonlocal done
        async with semaphore:
            try:
                trades.extend(await _fetch_sgg(client, cache, key, sgg.code, month_list, today, pacer))
            except (httpx.HTTPError, RuntimeError, ET.ParseError, ValueError, RateLimited):
                failed.append(sgg.code)
        done += 1
        on_progress(done, len(sggs))

    await asyncio.gather(*(one(s) for s in sggs))
    return trades, sorted(failed)
