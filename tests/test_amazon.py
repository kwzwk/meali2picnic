"""Amazon pantry cart: ASIN parsing, cart link, plan/map/cart endpoints (offline)."""
from urllib.parse import parse_qs, urlparse

import test_app  # noqa: F401  (sets env, fakes Mealie/Picnic)
from test_app import RECIPES, client

from app import amazon, main


def test_extract_asin():
    assert amazon.extract_asin("B000FQ3LIQ") == "B000FQ3LIQ"
    assert amazon.extract_asin("b000fq3liq") == "B000FQ3LIQ"
    assert amazon.extract_asin("https://www.amazon.de/Barilla-Spaghetti/dp/B000FQ3LIQ/ref=sr_1_1?k=x") == "B000FQ3LIQ"
    assert amazon.extract_asin("https://www.amazon.de/gp/product/B000FQ3LIQ?th=1") == "B000FQ3LIQ"
    assert amazon.extract_asin("https://amzn.eu/d/abc") is None
    assert amazon.extract_asin("hello") is None


def test_cart_url_and_domain(monkeypatch):
    monkeypatch.delenv("AMAZON_DOMAIN", raising=False)
    monkeypatch.delenv("AMAZON_ASSOCIATE_TAG", raising=False)
    url = amazon.cart_url([("B000FQ3LIQ", 2), ("B07XYZ1234", 0), ("B01ABCDEFG", 1)])
    u = urlparse(url)
    assert (u.netloc, u.path) == ("www.amazon.de", "/gp/aws/cart/add.html")
    assert parse_qs(u.query) == {"ASIN.1": ["B000FQ3LIQ"], "Quantity.1": ["2"],
                                 "ASIN.2": ["B01ABCDEFG"], "Quantity.2": ["1"]}   # zero line dropped, renumbered
    monkeypatch.setenv("AMAZON_ASSOCIATE_TAG", "kai-21")
    assert "AssociateTag=kai-21" in amazon.cart_url([("B000FQ3LIQ", 1)])
    monkeypatch.setenv("AMAZON_DOMAIN", "https://amazon.nl/")
    assert amazon.domain() == "www.amazon.nl"
    monkeypatch.setenv("AMAZON_DOMAIN", "off")
    assert not amazon.enabled()


def test_default_count():
    assert amazon.default_count({"": 3.0}) == 3          # 3 cans
    assert amazon.default_count({"g": 500.0}) == 1       # weighed: one pack
    assert amazon.default_count({}) == 1


def test_amazon_flow_without_picnic_login():
    saved, main.picnic.api = main.picnic.api, None       # Amazon must not need Picnic
    try:
        assert client.get("/api/status").json()["amazon_domain"] == "www.amazon.de"
        RECIPES["pantry"] = {"name": "Pantry", "slug": "pantry", "recipeIngredient": [
            {"quantity": 2, "unit": None, "food": {"name": "canned tomatoes"}},
            {"quantity": 500, "unit": {"name": "g"}, "food": {"name": "Spaghetti"}},
            {"quantity": 1, "unit": None, "food": {"name": "Salz"}}]}
        rows = {r["ingredient"]: r for r in client.post("/api/amazon/plan", json={"recipes": ["pantry"]}).json()["rows"]}
        assert set(rows) == {"canned tomatoes", "spaghetti"}             # salt skipped
        assert rows["spaghetti"]["asin"] is None and rows["spaghetti"]["count"] == 0
        assert rows["spaghetti"]["search_url"].startswith("https://www.amazon.de/s?k=")

        bad = client.post("/api/amazon/map", json={"ingredient": "spaghetti", "product": "not a link"})
        assert bad.status_code == 400
        r = client.post("/api/amazon/map", json={"ingredient": "spaghetti",
                                                 "product": "https://www.amazon.de/dp/B000FQ3LIQ", "amounts": {"g": 500}}).json()
        assert r["asin"] == "B000FQ3LIQ" and r["count"] == 1
        client.post("/api/amazon/map", json={"ingredient": "canned tomatoes", "product": "B01ABCDEFG"})

        rows = {r["ingredient"]: r for r in client.post("/api/amazon/plan", json={"recipes": ["pantry"]}).json()["rows"]}
        assert rows["spaghetti"]["asin"] == "B000FQ3LIQ"                 # remembered
        assert rows["canned tomatoes"]["count"] == 2

        cart = client.post("/api/amazon/cart", json={"lines": [{"asin": "B000FQ3LIQ", "count": 1},
                                                               {"asin": "B01ABCDEFG", "count": 2}]}).json()
        assert cart["items"] == 2 and "ASIN.2=B01ABCDEFG&Quantity.2=2" in cart["url"]
        assert client.post("/api/amazon/cart", json={"lines": []}).status_code == 400

        client.post("/api/amazon/map", json={"ingredient": "spaghetti", "product": ""})   # forget
        rows = {r["ingredient"]: r for r in client.post("/api/amazon/plan", json={"recipes": ["pantry"]}).json()["rows"]}
        assert rows["spaghetti"]["asin"] is None
    finally:
        main.picnic.api = saved


def test_plan_sends_amazon_matched_items_to_amazon():
    client.post("/api/amazon/map", json={"ingredient": "spaghetti", "product": "B000FQ3LIQ"})
    try:
        r = client.post("/api/plan", json={"recipes": ["pasta"]}).json()
        assert [x["ingredient"] for x in r["rows"]] == ["zwiebel"]              # Picnic gets the rest
        assert [x["ingredient"] for x in r["amazon_rows"]] == ["spaghetti"]
        assert r["amazon_rows"][0]["asin"] == "B000FQ3LIQ" and r["amazon_rows"][0]["count"] == 1
        assert r["rows"][0]["amazon_search_url"].startswith("https://www.amazon.de/s?k=")
    finally:
        client.post("/api/amazon/map", json={"ingredient": "spaghetti", "product": ""})
    r = client.post("/api/plan", json={"recipes": ["pasta"]}).json()
    assert {x["ingredient"] for x in r["rows"]} == {"spaghetti", "zwiebel"} and r["amazon_rows"] == []
