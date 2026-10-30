# 우리 집 어디로? Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 부부 직장 주소 2곳과 매매 예산을 받아 수도권 법정동을 통근·인프라·예산 점수로 추천하고 가격 흐름을 보여 주는 로컬 웹앱.

**Architecture:** FastAPI 서버가 외부 API(국토부·ODsay·카카오)를 `sources/*`로 호출하고 SQLite에 캐시한다. `recommend.py`는 외부 호출 없는 순수 함수로 후보를 좁히고 점수를 매기며, `pipeline.py`가 둘을 엮어 SSE로 진행 상황과 결과를 보낸다. 화면은 정적 HTML/JS + 카카오맵 SDK, 차트는 직접 그린 SVG.

**Tech Stack:** Python 3.12, uv, FastAPI, uvicorn, httpx(async), python-dotenv, pytest, pytest-asyncio, SQLite(표준 라이브러리), Kakao Maps JS SDK.

**Spec:** `home-finder/docs/superpowers/specs/2026-10-30-home-finder-design.md`

## Global Constraints
- 모든 경로는 `C:\Users\User\Desktop\삼성전자_DS_실습자료\실습자료\Ch2_실습용\home-finder\` 기준. 이 폴더 밖에서 작업하지 않는다(지역 코드 파일 복사만 예외: `../mcp-servers/real-estate-mcp/src/real_estate/resources/region_codes.txt`, MIT, 출처 표기).
- Python `>=3.12`, 패키지 관리는 `uv`만 사용. 실행: `uv run uvicorn app.main:app` → http://localhost:8000
- 환경변수: `DATA_GO_KR_API_KEY`, `ODSAY_API_KEY`, `KAKAO_REST_API_KEY`, `KAKAO_JS_KEY`. `KAKAO_JS_KEY`만 브라우저로 보낸다.
- `.env`, `data/cache.sqlite`, `.venv/`, `__pycache__/`는 git에 올리지 않는다.
- 금액 단위: 내부·응답 모두 **억 원 float** (소수 둘째 자리 반올림). 국토부 `dealAmount`는 만원 문자열("98,000" → 9.8).
- 면적 구간: `"59"` = 50 ≤ 전용 ≤ 65, `"84"` = 76 ≤ 전용 ≤ 90, `"all"` = 전체.
- 기간: 실거래 최근 12개월(이번 달 포함), 중위가·거래 수는 최근 6개월, 거래 3건 미만 동 제외.
- 후보 수: 거리 필터 30곳 → 인프라 15곳 → 결과 10곳. 최대 통근 기본 60분.
- 점수 기본 비중: 통근 0.5, 인프라 0.3, 예산 0.2. 통근 지표 = 합계 + 0.5 × |A−B|.
- 인프라 가중치/상한: 지하철역(SW8) 3/3, 학교(SC4) 2/5, 대형마트(MT1) 2/3, 병원(HP8) 1/20, 공원(키워드 "공원") 1/5. 반경 1000m.
- 캐시 TTL: 지난달 이전 실거래 영구, 이번 달 1일(86400s), 통근 30일, 인프라 30일, 지오코딩 영구. 통근 키 좌표는 소수 셋째 자리.
- 수치를 지어내지 않는다: 실패한 값은 누락·제외로 표시하고 추정치로 대체하지 않는다(유일한 예외: ODsay가 700m 이내라 경로를 주지 않을 때 도보 시간 = 직선거리 ÷ 4.5km/h).
- 화면 출처 문구: "국토교통부 실거래가 (YYYY-MM ~ YYYY-MM), ODsay, 카카오 로컬".

## Review Focus
1. 직장 주소가 여러 곳으로 검색될 때(예: "상암동") → 후보 최대 5개를 보여 주고 선택 전에는 추천을 실행하지 않는다. (Task 6 `test_geocode_returns_candidates`)
2. 예산이 매우 낮아 통과 동이 0곳 → "예산 내 동네 없음" + 중위가가 예산에 가장 가까운 3곳 참고 표시, 오류 없이 끝난다. (Task 3 `test_budget_filter_none_pass_returns_nearest`)
3. 같은 법정동명이 다른 시·군·구에 있음(예: 신사동 — 강남구/관악구) → 동 키는 `sgg_code:동명`이라 섞이지 않는다. (Task 3 `test_aggregate_same_dong_name_different_sgg`)
4. ODsay 한도 초과 등 일부 후보만 통근 실패 → 그 후보만 "통근 확인 불가"로 제외, 나머지로 순위. 전부 실패면 오류 메시지. (Task 6 `test_pipeline_partial_commute_failure`)
5. 모든 후보 점수가 같은 항목(정규화 분모 0) → 해당 항목 100점, NaN 없음. (Task 3 `test_normalize_all_equal`)

---

### Task 1: 프로젝트 뼈대, 설정, 캐시

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `app/__init__.py`, `app/config.py`, `app/cache.py`
- Test: `tests/test_cache.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `config.Settings` dataclass: `data_go_kr_key: str|None, odsay_key: str|None, kakao_rest_key: str|None, kakao_js_key: str|None`; `config.load_settings(env_path: Path|None=None) -> Settings`; `Settings.missing() -> list[str]` (빠진 환경변수 이름).
  - `cache.Cache(path: Path)`: `get(key: str) -> Any|None`, `set(key: str, value: Any, ttl: float|None) -> None` (ttl None = 영구), 시간 함수 주입용 `now: Callable[[], float] = time.time` 생성자 인자.

- [ ] **Step 1:** `git init`, `uv init --app` 대신 `pyproject.toml` 직접 작성(의존성: fastapi, uvicorn, httpx, python-dotenv / dev: pytest, pytest-asyncio; `[tool.pytest.ini_options] asyncio_mode="auto"`, `markers=["live: 실제 API 호출"]`, `addopts="-m 'not live'"`). `.gitignore`는 Global Constraints 목록. `.env.example`은 키 4개를 빈 값으로. `uv sync`.
- [ ] **Step 2: 실패 테스트 작성**
```python
def test_set_get_roundtrip(tmp_path):
    c = Cache(tmp_path/"c.sqlite"); c.set("k", {"a": 1}, ttl=None)
    assert c.get("k") == {"a": 1}
def test_ttl_expiry(tmp_path):
    t = [1000.0]; c = Cache(tmp_path/"c.sqlite", now=lambda: t[0])
    c.set("k", 1, ttl=10); t[0] = 1009; assert c.get("k") == 1
    t[0] = 1011; assert c.get("k") is None
def test_permanent_never_expires(tmp_path):
    t = [0.0]; c = Cache(tmp_path/"c.sqlite", now=lambda: t[0])
    c.set("k", 1, ttl=None); t[0] = 1e12; assert c.get("k") == 1
def test_missing_keys(tmp_path):
    p = tmp_path/".env"; p.write_text("DATA_GO_KR_API_KEY=x\n")
    s = load_settings(p)  # 실제 환경변수 영향 없도록 monkeypatch.delenv로 4개 제거 후 호출
    assert s.missing() == ["ODSAY_API_KEY", "KAKAO_REST_API_KEY", "KAKAO_JS_KEY"]
```
- [ ] **Step 3:** `uv run pytest -q` → FAIL (import error)
- [ ] **Step 4:** 구현. 테이블 `cache(key TEXT PRIMARY KEY, value TEXT, fetched_at REAL, ttl REAL)`, value는 `json.dumps(ensure_ascii=False)`. `load_settings`는 `dotenv_values(env_path or 앱루트/.env)`를 우선하고 없으면 `os.environ`.
- [ ] **Step 5:** `uv run pytest -q` → PASS
- [ ] **Step 6:** `git add -A && git commit -m "feat: project scaffold, settings, sqlite cache"`

### Task 2: 지역 목록과 국토부 실거래가

**Files:**
- Create: `app/resources/region_codes.txt`(복사), `app/resources/NOTICE.md`(출처·MIT 문구), `app/regions.py`, `app/sources/__init__.py`, `app/sources/molit.py`
- Test: `tests/test_regions.py`, `tests/test_molit.py`, `tests/fixtures/molit_sample.xml`

**Interfaces:**
- Consumes: `Cache`
- Produces:
  - `regions.Sgg(code: str, name: str)` (name 예: "경기도 성남시 분당구"); `regions.capital_area_sggs() -> list[Sgg]`
  - `molit.Trade(sgg_code: str, dong: str, apt: str, area_m2: float, price_eok: float, ym: str, day: int)` (ym "YYYY-MM")
  - `molit.parse_trades(xml: str, sgg_code: str) -> list[Trade]`
  - `molit.in_band(area_m2: float, band: str) -> bool`
  - `molit.recent_months(today: date, n: int = 12) -> list[str]` (오래된 → 최근, "YYYYMM")
  - `async molit.fetch_month(client: httpx.AsyncClient, cache: Cache, key: str, sgg_code: str, yyyymm: str, today: date) -> list[Trade]`
  - `async molit.fetch_all(client, cache, key, sggs: list[Sgg], today: date, on_progress: Callable[[int,int],None]) -> tuple[list[Trade], list[str]]` (거래, 실패한 sgg_code 목록)

- [ ] **Step 1:** 실제 응답 샘플 저장: 강남구 11680, 202609를 `numOfRows=5`로 호출해 `tests/fixtures/molit_sample.xml`로 저장(키 문자열이 파일에 없는지 `grep` 확인). 해제 거래 행이 없으면 샘플 하나의 `<cdealType>`을 `O`로 바꿔 둔다.
- [ ] **Step 2: 실패 테스트 작성**
```python
def test_capital_area_sggs():
    codes = {s.code for s in capital_area_sggs()}
    assert len(codes) == 82
    assert {"11680", "28710", "41111", "41135"} <= codes   # 강남구, 강화군, 수원 장안구, 성남 분당구
    assert "41110" not in codes and "11000" not in codes    # 하위 구가 있는 시, 시도 단위 제외
def test_parse_trades_amount_and_cancel():
    ts = parse_trades(FIXTURE, "11680")
    assert all(isinstance(t.price_eok, float) for t in ts)
    assert len(ts) == 기대행수 - 해제행수          # 픽스처에 맞춰 상수로
    # "98,000" → 9.8 변환은 별도 단위 테스트: _to_eok("98,000") == 9.8, _to_eok(" 125,500 ") == 12.55
def test_in_band():
    assert in_band(59.9, "59") and in_band(50, "59") and not in_band(66, "59")
    assert in_band(84.97, "84") and not in_band(75.9, "84") and in_band(200, "all")
def test_recent_months():
    assert recent_months(date(2026,10,30), 3) == ["202608", "202609", "202610"]
    assert recent_months(date(2026,1,5), 2) == ["202512", "202601"]
async def test_fetch_month_ttl(tmp_path):  # httpx.MockTransport로 응답, 호출 횟수 카운트
    # 지난달(202609) 두 번 호출 → 네트워크 1회, cache 엔트리 ttl None
    # 이번 달(202610) → ttl 86400
async def test_fetch_all_reports_failed(tmp_path):
    # 한 sgg만 500 응답 → 2회 재시도 후 failed == ["41135"], 나머지 거래는 반환
```
- [ ] **Step 3:** `uv run pytest tests/test_regions.py tests/test_molit.py -q` → FAIL
- [ ] **Step 4:** 구현.
  - regions 규칙: `폐지여부=="존재"`, 코드가 11/28/41로 시작, 뒤 5자리 `00000`, 3~5자리 ≠ `000`. 같은 앞 4자리 코드가 2개 이상이고 5번째 자리가 `0`인 코드(예: 41110)는 제외.
  - molit: 엔드포인트 `https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade`, 파라미터 `serviceKey, LAWD_CD, DEAL_YMD, numOfRows=1000, pageNo`(totalCount가 1000 초과면 페이지 반복). 필드 `umdNm, aptNm, excluUseAr, dealAmount, dealYear, dealMonth, dealDay, cdealType`(값이 `O`면 제외). XML 파싱은 `defusedxml` 대신 표준 `xml.etree`로 충분(신뢰된 공공 API지만 의존성 최소화). `resultCode != "000"`이면 예외. 캐시 키 `molit:{sgg}:{yyyymm}`, 값은 Trade dict 목록, ttl은 yyyymm < today 월이면 None, 아니면 86400. `fetch_all`은 `asyncio.Semaphore(10)`, 예외 시 2회 재시도(0.5s, 1s).
- [ ] **Step 5:** PASS 확인
- [ ] **Step 6:** commit `feat: capital-area regions and MOLIT trade fetcher`

### Task 3: 집계·필터·점수 (순수 로직)

**Files:**
- Create: `app/recommend.py`
- Test: `tests/test_recommend.py`

**Interfaces:**
- Consumes: `Trade`
- Produces:
  - `DongStat(key: str, sgg_code: str, sgg_name: str, dong: str, median_6m: float, deals_6m: int, trend: list[dict], trend_change_pct: float|None)` — `key = f"{sgg_code}:{dong}"`, `trend` 항목 `{"ym": "YYYY-MM", "median": float|None, "count": int}` 12개(거래 없는 달 median None)
  - `aggregate(trades: list[Trade], band: str, months: list[str], sgg_names: dict[str,str]) -> list[DongStat]` (months = recent_months 결과, 최근 6개 = months[-6:])
  - `budget_filter(stats, budget_eok: float, min_deals: int = 3) -> BudgetResult` — `BudgetResult(kept: list[DongStat], low_deal_excluded: int, nearest_over_budget: list[DongStat])` (kept가 비면 중위가가 예산에 가까운 3곳)
  - `haversine_km(a: tuple[float,float], b: tuple[float,float]) -> float` (lat, lng)
  - `top_by_distance(items: list[tuple[DongStat, tuple[float,float]]], wa, wb, n: int = 30) -> list[tuple[DongStat, tuple[float,float]]]`
  - `Commute(minutes: int, transfers: int, summary: str, walk: bool)`
  - `Infra(subway: int, school: int, mart: int, hospital: int, park: int)`
  - `Candidate(stat: DongStat, coord: tuple[float,float], commute_a: Commute, commute_b: Commute, infra: Infra|None)`
  - `commute_metric(a: Commute, b: Commute) -> float` = a+b+0.5|a−b|
  - `infra_raw(i: Infra) -> float` (Global Constraints 가중치/상한: Σ w × min(n,cap)/cap)
  - `normalize(values: list[float], higher_is_better: bool) -> list[float]` (0~100, 전부 같으면 모두 100)
  - `score(cands: list[Candidate], budget_eok: float, weights: dict = {"commute":.5,"infra":.3,"budget":.2}) -> list[dict]` — 스펙 §5 결과 JSON 형태, total 내림차순, `scores` 각 항목 정수 반올림

- [ ] **Step 1: 실패 테스트 작성**
```python
def test_aggregate_median_and_band(): # 같은 동 59형 3건(8.0, 9.0, 10.0) + 84형 1건 → band "59" median_6m == 9.0, deals_6m == 3
def test_aggregate_only_last6_for_median(): # 7개월 전 거래는 trend에는 있고 median_6m/deals_6m엔 없음
def test_aggregate_same_dong_name_different_sgg(): # "신사동" 11680, 11620 → DongStat 2개, key 다름
def test_trend_change_pct(): # 처음 3개월 중위 8.0, 최근 3개월 중위 8.4 → 5.0 ; 한쪽 구간 거래 0 → None
def test_budget_filter_min_deals(): # deals 2인 동은 kept 제외, low_deal_excluded == 1
def test_budget_filter_none_pass_returns_nearest(): # 예산 1.0억, 중위 5,6,7,8 → kept == [], nearest 중위 [5,6,7]
def test_haversine(): # 서울역(37.5547,126.9707)–강남역(37.4979,127.0276) ≈ 8.0 km (abs < 0.3)
def test_top_by_distance_orders_by_sum(): # n=2 → 합이 가장 작은 2곳
def test_commute_metric_fairness(): # (40,40) → 80 ; (20,60) → 100 ; 공평한 쪽이 작다
def test_normalize_all_equal(): # normalize([5,5,5], True) == [100,100,100]
def test_normalize_direction(): # normalize([1,2,3], False) == [100,50,0]
def test_score_weights_change_ranking(): # 통근 좋은 A / 인프라 좋은 B → 기본 비중 A 1위, {"commute":0,"infra":1,"budget":0} B 1위
def test_score_infra_none_scores_zero_infra(): # infra None 후보의 infra 점수 0, 결과 dict의 "infra"는 None
```
- [ ] **Step 2:** FAIL 확인 → **Step 3:** 구현(중위는 `statistics.median`, 금액 소수 둘째 자리 반올림) → **Step 4:** PASS → **Step 5:** commit `feat: aggregation, filters, scoring`

### Task 4: 카카오 (지오코딩·인프라)

**Files:**
- Create: `app/sources/kakao.py`
- Test: `tests/test_kakao.py`

**Interfaces:**
- Consumes: `Cache`, `Infra`
- Produces:
  - `Place(name: str, address: str, lat: float, lng: float)`
  - `async geocode(client, cache, key: str, query: str, limit: int = 5) -> list[Place]` — `address.json` 결과가 있으면 그것, 없으면 `keyword.json`. 캐시 `kakao:geo:{query}` 영구.
  - `async infra_counts(client, cache, key: str, dong_key: str, lat: float, lng: float) -> Infra` — `category.json`(SW8, SC4, MT1, HP8) + `keyword.json`(query "공원"), 각 `radius=1000&size=1`, `meta.total_count` 사용. 캐시 `kakao:infra:{dong_key}` 30일.
- 헤더 `Authorization: KakaoAK {key}`, 베이스 `https://dapi.kakao.com/v2/local/`. 좌표 응답은 x=lng, y=lat 문자열.

- [ ] **Step 1: 실패 테스트(MockTransport)**
```python
async def test_geocode_address_first(): # address.json documents 1개 → keyword 호출 없음, Place.lat == float(y)
async def test_geocode_fallback_keyword(): # address 빈 결과 → keyword 결과 3개 반환
async def test_geocode_cached(): # 같은 query 2회 → 네트워크 1회
async def test_infra_counts(): # 각 카테고리 total_count 2,5,1,12 / 공원 3 → Infra(2,5,1,12,3), 요청 5회, radius=1000
```
- [ ] **Step 2–5:** FAIL → 구현 → PASS → commit `feat: kakao geocode and infra counts`

### Task 5: ODsay 통근

**Files:**
- Create: `app/sources/odsay.py`
- Test: `tests/test_odsay.py`, `tests/fixtures/odsay_sample.json`

**Interfaces:**
- Consumes: `Cache`, `Commute`, `haversine_km`
- Produces:
  - `class CommuteUnavailable(Exception)`
  - `async commute(client, cache, key: str, origin: tuple[float,float], dest: tuple[float,float]) -> Commute` — origin=동 좌표(lat,lng), dest=직장.
- 호출: `GET https://api.odsay.com/v1/api/searchPubTransPathT?apiKey=..&SX={lng}&SY={lat}&EX=..&EY=..` (apiKey는 URL 인코딩). `result.path` 중 `info.totalTime` 최소 경로 선택; `transfers = busTransitCount + subwayTransitCount - 1`(최소 0); `summary`는 `subPath` 중 trafficType 1(지하철)/2(버스)의 `lane[0].name` 또는 `busNo`를 " → "로 연결.
- 응답 `error`가 있고 code `-98`(출발·도착 700m 이내) → `Commute(round(dist_km/4.5*60), 0, "도보", True)`. 다른 error나 HTTP 오류 → `CommuteUnavailable`. 캐시 키 `odsay:{lat:.3f},{lng:.3f}>{lat:.3f},{lng:.3f}` 30일. 실패는 캐시하지 않는다.
- 구현 직전에 ODsay 문서(lab.odsay.com 가이드)에서 `-98` 코드와 출발 시각 옵션 유무를 확인하고, 다르면 이 Task의 테스트를 문서에 맞게 고친 뒤 그 사실을 커밋 메시지에 적는다.

- [ ] **Step 1:** 실제 응답 1건 저장(키 확보 후, 서울역→강남역) → `tests/fixtures/odsay_sample.json`. 키가 아직 없으면 ODsay 문서 예시 응답으로 만들고 Task 8에서 실제 응답으로 교체.
- [ ] **Step 2: 실패 테스트**
```python
async def test_commute_picks_min_time(): # 픽스처 path 중 최소 totalTime == Commute.minutes, summary 비어 있지 않음
async def test_commute_walk_when_too_close(): # {"error":[{"code":"-98",...}]} , 거리 0.45km → minutes 6, walk True
async def test_commute_other_error_raises(): # code "-8" → CommuteUnavailable
async def test_commute_failure_not_cached(): # 1회차 오류, 2회차 정상 → 2회차 결과 반환
```
- [ ] **Step 3–5:** FAIL → 구현 → PASS → commit `feat: odsay commute`

### Task 6: 파이프라인과 API

**Files:**
- Create: `app/pipeline.py`, `app/main.py`, `app/warmup.py`
- Test: `tests/test_pipeline.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: Task 1–5 전부
- Produces:
  - `RecommendRequest`(pydantic): `budget_eok: float (>0)`, `band: Literal["59","84","all"]`, `work_a: tuple[float,float]`, `work_b: tuple[float,float]`, `max_a: int = 60`, `max_b: int = 60`
  - `Deps` dataclass: `client, cache, settings, today: date`, 그리고 테스트에서 교체할 수 있는 함수 필드 `fetch_all, geocode, commute, infra_counts` (기본값은 sources 함수)
  - `async run_recommend(req, deps, emit: Callable[[str, dict], Awaitable[None]]) -> dict` — emit 이벤트 `("progress", {"stage": "molit"|"geocode"|"commute"|"infra", "done": int, "total": int})`. 반환:
    `{"results": [...최대 10], "notes": {"failed_sggs": [...], "low_deal_excluded": int, "commute_unavailable": int, "nearest_over_budget": [...]}, "period": {"from": "YYYY-MM", "to": "YYYY-MM"}}`
  - HTTP: `GET /` (static/index.html), `GET /api/config` → `{"kakao_js_key": str|None, "missing": [...]}`, `GET /api/geocode?q=` → `{"candidates": [Place...]}`, `POST /api/recommend` → `text/event-stream` (`event: progress|result|error`, `data: JSON`)
  - `uv run python -m app.warmup` — 82개 sgg 실거래 + 동 좌표 캐시 채우기, 진행률 출력
- 흐름은 스펙 §4 ①~⑦ 그대로: fetch_all → aggregate(band) → budget_filter → 동 지오코딩(`f"{sgg_name} {dong}"`, 결과 첫 번째, 실패 동은 건너뜀) → top_by_distance(30) → 각 후보 A·B commute(최대 통근 초과 제외, CommuteUnavailable 개수 집계) → commute_metric 상위 15 infra_counts → score → 상위 10.
- 필수 키 누락 시: DATA_GO_KR/ODSAY/KAKAO_REST 중 하나라도 없으면 `/api/recommend`는 `event: error` `{"message": "<키 이름> 없음: <기능> 불가"}`.

- [ ] **Step 1: 실패 테스트 (가짜 Deps)**
```python
async def test_pipeline_happy_path(): # 가짜 거래 5개 동, 전부 통과 → results 길이 5, total 내림차순, progress 이벤트에 4개 stage 모두 등장
async def test_pipeline_partial_commute_failure(): # 2곳 commute가 CommuteUnavailable → results 3곳, notes.commute_unavailable == 2
async def test_pipeline_all_commute_fail_errors(): # 전부 실패 → 예외 메시지 "통근 시간을 확인할 수 있는 후보가 없습니다"
async def test_pipeline_max_commute_filter(): # max_a=30, A 통근 45분 후보 제외
async def test_pipeline_budget_none(): # results == [], notes.nearest_over_budget 길이 3
def test_geocode_returns_candidates(): # TestClient, 가짜 geocode 3개 → /api/geocode candidates 3개
def test_config_hides_server_keys(): # /api/config 응답 문자열에 DATA_GO_KR 키 값이 없음
def test_recommend_missing_key_sse_error(): # ODSAY 없음 → 스트림에 "event: error" 와 "ODSAY_API_KEY 없음"
```
- [ ] **Step 2–5:** FAIL → 구현(`StreamingResponse`, `asyncio.Queue`로 emit→스트림 연결, lifespan에서 httpx.AsyncClient·Cache 생성) → PASS → commit `feat: recommend pipeline, API, warmup`

### Task 7: 화면

**Files:**
- Create: `app/static/index.html`, `app/static/app.js`, `app/static/style.css`
- Test: Playwright 수동 확인(Step 3), 브라우저 로직 중 `recomputeTotals`만 순수 함수로 두고 index에서 사용

**Interfaces:**
- Consumes: Task 6 HTTP API
- 화면 요구(스펙 §6): 왼쪽 입력(예산·면적 라디오·직장 A/B·최대 통근 A/B·추천 버튼)·비중 슬라이더 3개·결과 목록, 오른쪽 카카오 지도. 좁은 화면(<900px) 세로 배치.
  - 직장 입력 → `/api/geocode` → 후보 1개면 자동 선택, 여러 개면 드롭다운으로 선택해야 추천 버튼 활성.
  - 추천: `fetch` POST 후 `response.body.getReader()`로 SSE 파싱, 진행 문구 "실거래가 수집 중 (34/82)" 등.
  - 결과: 지도에 직장 마커 2, 동네 번호 마커(점수 색 진하기), 클릭 시 반경 1km 원 + 상세 패널(SVG 가격 차트: 꺾은선 중위가 + 막대 거래 수, median null 달은 선 끊김 / A·B 통근 분·환승·노선 / 인프라 5종 / 점수 구성 가로 막대 / 출처·기간).
  - 슬라이더 변경 → `recomputeTotals(results, weights)`로 재정렬, 서버 호출 없음.
  - `notes` 표시: 누락 시·군·구 수, 거래 적어 제외 수, 통근 확인 불가 수, 예산 내 없음 시 참고 3곳.
  - `/api/config.missing`이 있으면 상단 경고 띠. 카카오 SDK: `//dapi.kakao.com/v2/maps/sdk.js?appkey={kakao_js_key}&autoload=false`. 키 없으면 지도 영역에 "KAKAO_JS_KEY 없음" 표시하고 목록은 동작.
  - 색: CSS 변수 토큰, `prefers-color-scheme: dark` 대응.

- [ ] **Step 1:** 구현
- [ ] **Step 2:** `uv run uvicorn app.main:app` 실행
- [ ] **Step 3:** Playwright: http://localhost:8000 → A "강남역", B "상암DMC" 입력·선택, 예산 8, 59㎡ → 추천 → 결과 1개 이상, 마커 표시, 첫 결과 클릭 시 SVG 차트 존재, 슬라이더 인프라 100으로 바꾸면 순서 변경 여부 확인, 화면 폭 390px에서 가로 스크롤 없음. 스크린샷 `docs/screenshots/`에 저장.
- [ ] **Step 4:** commit `feat: web UI`

### Task 8: 실제 API 확인, README, GitHub 업로드

**Files:**
- Create: `tests/test_live.py`, `README.md`
- Modify: `tests/fixtures/odsay_sample.json`(문서 예시로 만들었으면 실제 응답으로)

- [ ] **Step 1:** `tests/test_live.py`(`@pytest.mark.live`): 국토부 11680/지난달 1건 이상, 카카오 geocode("강남역") 1건 이상, ODsay 서울역→강남역 minutes 10~60. 실행 `uv run pytest -m live -q` → PASS (키 없는 API는 skip, 그 사실을 보고)
- [ ] **Step 2:** `uv run python -m app.warmup` 1회 실행, 소요 시간 기록
- [ ] **Step 3:** README: 소개·스크린샷, 키 4종 발급(스펙 §2 표), `.env` 작성, `uv sync`·`warmup`·실행 명령, 데이터 출처·한계(중위가는 법정동 대표값, 3개월 이동평균 아님 등), 지역 코드 출처(MIT).
- [ ] **Step 4:** 키 유출 검사: `git grep -nE "$(키 값들)"`이 아니라, 키 값을 셸 변수로 읽어 `git grep -F -q`로 존재 여부만 확인(값 출력 금지). 결과 없음이어야 통과. `git status`에 `.env`, `cache.sqlite` 없음 확인.
- [ ] **Step 5:** 사용자에게 저장소 이름·공개 여부 확인 → `gh auth status`(미로그인 시 사용자에게 `! gh auth login` 요청) → `gh repo create <이름> --<public|private> --source . --push`
- [ ] **Step 6:** 저장소 URL을 사용자에게 전달
