"""Offline tests: mocked Mealie + Picnic. Run: python -m pytest -q"""
import base64
import os
import sys
import tempfile
from types import SimpleNamespace as NS

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["MEALIE_URL"] = "http://mealie.test"
os.environ["MEALIE_TOKEN"] = "x"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402

from app import core, main  # noqa: E402

RECIPES = {
    "pasta": {"name": "Pasta", "slug": "pasta", "recipeIngredient": [
        {"quantity": 400, "unit": {"name": "g"}, "food": {"name": "Spaghetti"}},
        {"quantity": 2, "unit": None, "food": {"name": "Zwiebel"}},
        {"quantity": 1, "unit": {"name": "TL"}, "food": {"name": "Salz"}},
    ]},
    "soup": {"name": "Soup", "slug": "soup", "recipeIngredient": [
        {"quantity": 0.5, "unit": {"name": "kg"}, "food": {"name": "spaghetti"}},
        {"quantity": 3, "unit": None, "food": {"name": "Zwiebel"}},
    ]},
}
CATALOG = {
    "spaghetti": [NS(id="s1", name="Barilla Spaghetti", display_price=199, unit_quantity="500 g"),
                  NS(id="s2", name="Bio Spaghetti", display_price=249, unit_quantity="250 g")],
    "zwiebel": [NS(id="z1", name="Zwiebeln", display_price=129, unit_quantity="1 kg")],
}
ADDED = []


class FakeAPI:
    def search(self, term):
        return NS(items=CATALOG.get(term, []))

    def get_article(self, _id):
        return None

    def clear_cart(self):
        ADDED.clear()

    def add_product(self, pid, count):
        ADDED.append((pid, count))

    def get_cart(self):
        return NS(total_count=sum(c for _, c in ADDED), total_price=1234)

    def get_user(self):
        return {}


class FakeMealie:
    def recipe(self, slug):
        return RECIPES[slug]

    def search_recipes(self, text="", limit=30):
        return [r for r in RECIPES.values() if text.lower() in r["name"].lower()]

    def shopping_lists(self):
        return [{"id": "l1", "name": "Weekly"}]

    def shopping_list(self, n):
        return {"listItems": []}


main.mealie = lambda: FakeMealie()
main.picnic.api = FakeAPI()
client = TestClient(main.app)


def test_pack_maths():
    assert core.pack_size("1 kg") == (1000.0, "g")
    assert core.packs_needed({"g": 900.0}, "500 g") == (2, "")
    assert core.packs_needed({"": 5.0}, "1 kg")[1]            # mismatch flagged
    assert core.packs_needed({"": 0.0}, "1 kg")[0] == 1       # no amount -> 1


def test_status_and_recipes():
    assert client.get("/api/status").json()["picnic_logged_in"] is True
    assert [r["slug"] for r in client.get("/api/recipes?q=soup").json()] == ["soup"]
    assert client.get("/api/shopping-lists").json()[0]["name"] == "Weekly"


def test_plan_merges_and_matches():
    rows = client.post("/api/plan", json={"recipes": ["pasta", "soup"]}).json()["rows"]
    by = {r["ingredient"]: r for r in rows}
    assert "salz" not in by                                    # skipped staple
    assert by["spaghetti"]["selected"] == "s1" and by["spaghetti"]["count"] == 2   # 900 g / 500 g
    assert len(by["spaghetti"]["candidates"]) == 2
    assert by["zwiebel"]["note"]                               # 5 pieces vs 1 kg bag


def test_plan_needs_a_source():
    assert client.post("/api/plan", json={}).status_code == 400


def test_cart_adds_and_remembers_choice():
    ADDED.clear()
    r = client.post("/api/cart", json={"lines": [
        {"ingredient": "spaghetti", "product_id": "s2", "count": 4},
        {"ingredient": "zwiebel", "product_id": "z1", "count": 0},   # skipped
    ]})
    assert r.json() == {"total_count": 4, "total_price": 12.34}
    assert ADDED == [("s2", 4)]
    rows = client.post("/api/plan", json={"recipes": ["pasta"]}).json()["rows"]
    sp = next(r for r in rows if r["ingredient"] == "spaghetti")
    assert sp["selected"] == "s2" and sp["remembered"]         # remembered pick wins over top hit


def test_not_logged_in():
    saved, main.picnic.api = main.picnic.api, None
    try:
        assert client.post("/api/plan", json={"recipes": ["pasta"]}).status_code == 401
    finally:
        main.picnic.api = saved


def test_basic_auth():
    main.APP_PASSWORD = "secret"
    try:
        assert client.get("/api/status").status_code == 401
        assert client.get("/health").status_code == 200        # healthcheck stays open
        good = {"Authorization": "Basic " + base64.b64encode(b"admin:secret").decode()}
        bad = {"Authorization": "Basic " + base64.b64encode(b"admin:nope").decode()}
        assert client.get("/api/status", headers=good).status_code == 200
        assert client.get("/api/status", headers=bad).status_code == 401
    finally:
        main.APP_PASSWORD = ""


def test_index_served():
    r = client.get("/")
    assert r.status_code == 200 and "meali" in r.text
