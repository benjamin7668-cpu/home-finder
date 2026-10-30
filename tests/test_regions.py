from app.regions import capital_area_sggs


def test_capital_area_sggs_count_and_members():
    sggs = capital_area_sggs()
    codes = {s.code for s in sggs}
    assert len(codes) == 83
    assert {"11680", "28710", "41111", "41135"} <= codes  # 강남구, 강화군, 수원 장안구, 성남 분당구


def test_excludes_parent_city_and_province():
    codes = {s.code for s in capital_area_sggs()}
    assert "41110" not in codes  # 수원시(하위 구 있음)
    assert "11000" not in codes and "41000" not in codes


def test_names():
    names = {s.code: s.name for s in capital_area_sggs()}
    assert names["11680"] == "서울특별시 강남구"
    assert names["41135"] == "경기도 성남시 분당구"


def test_incheon_2026_reorganization():
    # 2026-07-01 인천 개편: 국토부 실거래가는 과거 이력까지 새 코드로만 조회된다
    names = {s.code: s.name for s in capital_area_sggs()}
    assert names["28125"] == "인천광역시 제물포구"
    assert names["28155"] == "인천광역시 영종구"
    assert names["28275"] == "인천광역시 서해구"
    assert names["28290"] == "인천광역시 검단구"
    assert not {"28110", "28140", "28260"} & set(names)


def test_names_have_no_trailing_space():
    assert all(s.name == s.name.strip() for s in capital_area_sggs())
