"""실제 API를 1회씩 호출해 응답 형식을 확인한다: uv run pytest -m live -q"""

from datetime import date

import httpx
import pytest

from app.cache import Cache
from app.config import load_settings
from app.sources import kakao, molit, odsay

pytestmark = pytest.mark.live
S = load_settings()
SEOUL_STN = (37.5547, 126.9707)
GANGNAM = (37.4979, 127.0276)


def _need(value, name):
    if not value:
        pytest.skip(f"{name} 없음")


async def test_molit_live(tmp_path):
    _need(S.data_go_kr_key, "DATA_GO_KR_API_KEY")
    last_month = molit.recent_months(date.today(), 2)[0]
    async with httpx.AsyncClient() as c:
        trades = await molit.fetch_month(c, Cache(tmp_path / "c.sqlite"), S.data_go_kr_key, "11680", last_month, date.today())
    assert trades and all(t.price_eok > 0 for t in trades)


async def test_kakao_geocode_live(tmp_path):
    _need(S.kakao_rest_key, "KAKAO_REST_API_KEY")
    async with httpx.AsyncClient() as c:
        places = await kakao.geocode(c, Cache(tmp_path / "c.sqlite"), S.kakao_rest_key, "강남역")
    assert places and 37.4 < places[0].lat < 37.6


async def test_odsay_live(tmp_path):
    _need(S.odsay_key, "ODSAY_API_KEY")
    async with httpx.AsyncClient() as c:
        result = await odsay.commute(c, Cache(tmp_path / "c.sqlite"), S.odsay_key, SEOUL_STN, GANGNAM)
    assert 10 <= result.minutes <= 60
