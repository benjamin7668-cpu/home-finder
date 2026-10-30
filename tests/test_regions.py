from app.regions import capital_area_sggs


def test_capital_area_sggs_count_and_members():
    sggs = capital_area_sggs()
    codes = {s.code for s in sggs}
    assert len(codes) == 82
    assert {"11680", "28710", "41111", "41135"} <= codes  # 강남구, 강화군, 수원 장안구, 성남 분당구


def test_excludes_parent_city_and_province():
    codes = {s.code for s in capital_area_sggs()}
    assert "41110" not in codes  # 수원시(하위 구 있음)
    assert "11000" not in codes and "41000" not in codes


def test_names():
    names = {s.code: s.name for s in capital_area_sggs()}
    assert names["11680"] == "서울특별시 강남구"
    assert names["41135"] == "경기도 성남시 분당구"
