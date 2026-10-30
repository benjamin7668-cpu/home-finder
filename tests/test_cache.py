from app.cache import Cache


def test_set_get_roundtrip(tmp_path):
    c = Cache(tmp_path / "c.sqlite")
    c.set("k", {"a": 1, "이름": "강남"}, ttl=None)
    assert c.get("k") == {"a": 1, "이름": "강남"}


def test_missing_key_returns_none(tmp_path):
    assert Cache(tmp_path / "c.sqlite").get("nope") is None


def test_ttl_expiry(tmp_path):
    t = [1000.0]
    c = Cache(tmp_path / "c.sqlite", now=lambda: t[0])
    c.set("k", 1, ttl=10)
    t[0] = 1009
    assert c.get("k") == 1
    t[0] = 1011
    assert c.get("k") is None


def test_permanent_never_expires(tmp_path):
    t = [0.0]
    c = Cache(tmp_path / "c.sqlite", now=lambda: t[0])
    c.set("k", 1, ttl=None)
    t[0] = 1e12
    assert c.get("k") == 1


def test_overwrite(tmp_path):
    c = Cache(tmp_path / "c.sqlite")
    c.set("k", 1, ttl=None)
    c.set("k", 2, ttl=None)
    assert c.get("k") == 2
