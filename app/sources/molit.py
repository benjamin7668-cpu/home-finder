"""국토교통부 아파트 매매 실거래가 조회."""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import date
from typing import Callable

import httpx

from app.cache import Cache
from app.regions import Sgg

ENDPOINT = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"
PAGE_SIZE = 1000
CURRENT_MONTH_TTL = 86400
RETRY_DELAYS = (0.5, 1.0)
CONCURRENCY = 10
BANDS = {"59": (50.0, 65.0), "84": (76.0, 90.0)}


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


def parse_trades(xml: str, sgg_code: str) -> list[Trade]:
    items, _ = _parse(xml)
    return [_to_trade(item, sgg_code) for item in items if _text(item, "cdealType") != "O"]


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
    client: httpx.AsyncClient, cache: Cache, key: str, sgg_code: str, yyyymm: str, today: date
) -> list[Trade]:
    cache_key = f"molit:{sgg_code}:{yyyymm}"
    cached = cache.get(cache_key)
    if cached is not None:
        return [Trade(**t) for t in cached]

    trades: list[Trade] = []
    page = 1
    while True:
        resp = await client.get(
            ENDPOINT,
            params={"serviceKey": key, "LAWD_CD": sgg_code, "DEAL_YMD": yyyymm,
                    "numOfRows": PAGE_SIZE, "pageNo": page},
            timeout=30,
        )
        resp.raise_for_status()
        items, total = _parse(resp.content.decode("utf-8"))
        trades += [_to_trade(i, sgg_code) for i in items if _text(i, "cdealType") != "O"]
        if page * PAGE_SIZE >= total:
            break
        page += 1

    ttl = None if yyyymm < f"{today.year:04d}{today.month:02d}" else CURRENT_MONTH_TTL
    cache.set(cache_key, [asdict(t) for t in trades], ttl=ttl)
    return trades


async def _fetch_sgg(client, cache, key, sgg_code, months, today) -> list[Trade]:
    out: list[Trade] = []
    for yyyymm in months:
        for attempt, delay in enumerate((*RETRY_DELAYS, None)):
            try:
                out += await fetch_month(client, cache, key, sgg_code, yyyymm, today)
                break
            except (httpx.HTTPError, RuntimeError, ET.ParseError):
                if delay is None:
                    raise
                await asyncio.sleep(delay)
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
    trades: list[Trade] = []
    failed: list[str] = []
    done = 0

    async def one(sgg: Sgg) -> None:
        nonlocal done
        async with semaphore:
            try:
                trades.extend(await _fetch_sgg(client, cache, key, sgg.code, month_list, today))
            except (httpx.HTTPError, RuntimeError, ET.ParseError):
                failed.append(sgg.code)
        done += 1
        on_progress(done, len(sggs))

    await asyncio.gather(*(one(s) for s in sggs))
    return trades, sorted(failed)
