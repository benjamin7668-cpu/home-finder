from app.config import KEY_NAMES, load_settings


def _clear_env(monkeypatch):
    for name in KEY_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_missing_keys(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    p = tmp_path / ".env"
    p.write_text("DATA_GO_KR_API_KEY=x\n", encoding="utf-8")
    s = load_settings(p)
    assert s.data_go_kr_key == "x"
    assert s.missing() == ["ODSAY_API_KEY", "KAKAO_REST_API_KEY", "KAKAO_JS_KEY"]


def test_empty_value_counts_as_missing(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    p = tmp_path / ".env"
    p.write_text("DATA_GO_KR_API_KEY=\n", encoding="utf-8")
    assert "DATA_GO_KR_API_KEY" in load_settings(p).missing()


def test_env_var_fallback(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ODSAY_API_KEY", "o")
    s = load_settings(tmp_path / "none.env")
    assert s.odsay_key == "o"
