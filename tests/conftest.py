import pytest

from app.pacing import Pacer


@pytest.fixture(autouse=True)
def fast_pacers(monkeypatch):
    """단위 테스트에서는 요청 간격을 두지 않는다(간격 자체를 보는 테스트는 직접 다시 설정)."""
    monkeypatch.setattr("app.sources.molit.PACER", Pacer(0))
    monkeypatch.setattr("app.sources.odsay.PACER", Pacer(0))
