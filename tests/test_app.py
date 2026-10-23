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
        return NS(items=CATALOG.get(term.lower(), []))   # Picnic search is case-insensitive

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
    assert r.json() == {"total_count": 4, "total_price": 12.34, "warning": None}
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


def test_unwritable_data_dir_does_not_lose_login(monkeypatch):
    """A /data volume the app can't write to must not break login (this crashed in Docker)."""
    from pathlib import Path
    from app.picnic_session import PicnicSession

    def deny(*a, **k):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(Path, "write_text", deny)
    s = PicnicSession.__new__(PicnicSession)
    api = NS(session=NS(auth_token="tok"))
    warning = s._save(api, "DE")
    assert s.api is api                       # still logged in this session
    assert "Permission denied" in warning and "10001" in warning


# ---------------------------------------------------------------- translation
from app import translate as tr  # noqa: E402


def test_translate_basics():
    assert tr.translate("onion") == "Zwiebel"
    assert tr.translate("Garlic") == "Knoblauch"
    assert tr.translate("tomatoes") == "Tomate"                   # plural
    assert tr.translate("strawberries") == "Erdbeeren"
    assert tr.translate("large red onion") == "rote Zwiebel"      # descriptor dropped, phrase kept
    assert tr.translate("fresh chopped parsley") == "Petersilie"
    assert tr.translate("chicken breast, skinless") == "Hähnchenbrust"
    assert tr.translate("ground beef") == "Hackfleisch"
    assert tr.translate("head of lettuce") == "Salat"
    assert tr.translate("extra virgin olive oil") == "Olivenöl"
    assert tr.translate("fresh basil leaves") == "Basilikum"
    assert tr.translate("bay leaves") == "Lorbeerblätter"        # known phrase wins over dropping "leaves"


def test_translate_passes_german_and_unknown_through():
    assert tr.translate("Zwiebel") == "Zwiebel"
    assert tr.translate("Hähnchenbrustfilet") == "Hähnchenbrustfilet"
    assert tr.translate("gochujang") == "gochujang"


def test_translate_overrides_win(tmp_path):
    f = tmp_path / "t.txt"
    f.write_text("# mine\nonion = Gemüsezwiebel\nsumac = Sumach\n")
    ov = tr.load_overrides(f)
    assert tr.translate("onion", ov) == "Gemüsezwiebel"
    assert tr.translate("sumac", ov) == "Sumach"
    assert tr.load_overrides(tmp_path / "missing.txt") == {}


def test_plan_searches_in_german_and_research_endpoint():
    searched = []
    real = FakeAPI.search

    def spy(self, term):
        searched.append(term)
        return real(self, term)
    FakeAPI.search = spy
    try:
        RECIPES["eng"] = {"name": "Eng", "slug": "eng", "recipeIngredient": [
            {"quantity": 2, "unit": None, "food": {"name": "onions"}}]}
        rows = client.post("/api/plan", json={"recipes": ["eng"]}).json()["rows"]
        assert searched == ["Zwiebel"]                           # German term hits, no fallback search needed
    finally:
        FakeAPI.search = real
    assert rows[0]["search_term"] == "Zwiebel" and rows[0]["ingredient"] == "onions"
    assert rows[0]["selected"] == "z1"
    r = client.post("/api/research", json={"ingredient": "onions", "term": "zwiebel", "amounts": {"": 2.0}}).json()
    assert r["selected"] == "z1" and r["search_term"] == "zwiebel"
