import pytest
import server

def test_resolve_kinozed_url():
    url = "https://hd.kinozed.org/movie/ulica-straha-koroleva-vypusknogo-2025/"
    resolved = server.normalize_url(url)
    assert resolved == "https://api.nextembed.ws/embed/kp/5516260"

def test_resolve_balabolka_url():
    url = "https://balabolka.stravers.live/?kp=5516260&token=TEST_TOKEN"
    resolved = server.normalize_url(url)
    assert resolved == "https://api.nextembed.ws/embed/kp/5516260"

def test_inspect_kinozed_url():
    url = "https://hd.kinozed.org/movie/ulica-straha-koroleva-vypusknogo-2025/"
    info = server.inspect_url(url)
    assert info.get("ok") is True
    assert "Улица страха" in info.get("title", "")
    assert info.get("kind") == "movie"
    assert len(info.get("translations", [])) >= 3
    assert info.get("thumb") == "https://st.kp.yandex.net/images/film_iphone/iphone360_5516260.jpg"
