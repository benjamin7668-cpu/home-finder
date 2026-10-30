import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.sources.kakao import Place
from tests.fakes import SETTINGS, WORK_A, WORK_B, make_deps


def client(settings=SETTINGS, deps=None):
    deps = deps or make_deps()
    deps.settings = settings
    return TestClient(create_app(settings=settings, make_deps=lambda app: deps))


def sse_events(text):
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_geocode_returns_candidates():
    deps = make_deps()

    async def geocode(client, cache, key, query, limit=5):
        return [Place(f"상암{i}", "주소", 37.5, 126.9) for i in range(3)]

    deps.geocode = geocode
    with client(deps=deps) as c:
        r = c.get("/api/geocode", params={"q": "상암동"})
    assert r.status_code == 200
    assert [p["name"] for p in r.json()["candidates"]] == ["상암0", "상암1", "상암2"]


def test_config_hides_server_keys():
    s = Settings("SECRET_DATA_KEY", "SECRET_ODSAY", "SECRET_KAKAO_REST", "public-js")
    with client(settings=s) as c:
        body = c.get("/api/config").text
    assert "SECRET" not in body
    assert json.loads(body) == {"kakao_js_key": "public-js", "missing": []}


def test_recommend_streams_progress_and_result():
    with client() as c:
        r = c.post("/api/recommend", json={"budget_eok": 20, "band": "59", "work_a": WORK_A, "work_b": WORK_B})
    events = sse_events(r.text)
    assert r.headers["content-type"].startswith("text/event-stream")
    assert events[-1][0] == "result" and len(events[-1][1]["results"]) == 5
    assert any(name == "progress" for name, _ in events)


def test_recommend_missing_key_sse_error():
    s = Settings("D", None, "KR", "KJ")
    with client(settings=s) as c:
        r = c.post("/api/recommend", json={"budget_eok": 8, "band": "59", "work_a": WORK_A, "work_b": WORK_B})
    events = sse_events(r.text)
    assert events == [("error", {"message": "ODSAY_API_KEY 없음: 통근 시간 계산 불가"})]


def test_index_served():
    with client() as c:
        r = c.get("/")
    assert r.status_code == 200 and "<html" in r.text.lower()
