"""Offline tests for the Norish source: canned API responses, no network."""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import core  # noqa: E402

STORE = "11111111-1111-4111-8111-111111111111"
GROCERIES = [
    {"id": "g1", "name": "Spaghetti", "unit": "g", "amount": 400, "purchaseAmount": None,
     "isDone": False, "storeId": STORE},
    {"id": "g2", "name": "Onion", "unit": None, "amount": 1, "purchaseAmount": 3,
     "isDone": False, "storeId": None},
    {"id": "g3", "name": "Milk", "unit": "l", "amount": 1, "purchaseAmount": None,
     "isDone": True, "storeId": None},
]
RECIPE = {"id": "r1", "name": "Pasta", "recipeIngredients": [
    {"ingredientName": "Spaghetti", "amount": 0.5, "unit": "kg"},
    {"ingredientName": "Garlic", "amount": 2, "unit": None},
]}
PLANNED = [
    {"id": "p1", "date": "2026-11-02", "recipeId": "r1", "recipeName": "Pasta"},
    {"id": "p2", "date": "2026-10-30", "recipeId": "r2", "recipeName": "Old"},
    {"id": "p3", "date": "2026-11-20", "recipeId": "r3", "recipeName": "Later"},
]


class FakeNorish(core.Norish):
    def __init__(self):
        super().__init__("http://norish.test", "key")
        self.posts = []

    def _get(self, path, **params):
        return {"/groceries": GROCERIES, "/stores": [{"id": STORE, "name": "Picnic"}],
                "/recipes/r1": RECIPE, "/planned-recipes/week": PLANNED[:2],
                "/planned-recipes/month": PLANNED}[path]

    def _post(self, path, body):
        self.posts.append((path, body))
        return {"recipes": [{"id": "r1", "name": "Pasta"}], "total": 1, "nextCursor": None}


def test_auth_header_and_base_url():
    n = core.Norish("https://norish.example.com/", "secret")
    assert n.base == "https://norish.example.com/api/v1"
    assert n.s.headers["x-api-key"] == "secret"


def test_groceries_all_and_per_store():
    n = FakeNorish()
    assert [l["name"] for l in n.shopping_lists()] == ["All groceries", "Picnic"]
    ings = [p for p in map(core.parse_ingredient, n.shopping_list("all")["listItems"]) if p]
    by = {i["name"]: i for i in ings}
    assert "milk" not in by                                     # done items are skipped
    assert by["spaghetti"]["qty"] == 400 and by["spaghetti"]["unit"] == "g"
    assert by["onion"]["qty"] == 3                              # unitless: purchaseAmount
    picnic = n.shopping_list("Picnic")["listItems"]
    assert [i["food"]["name"] for i in picnic] == ["Spaghetti"]


def test_recipes_merge_with_groceries():
    n = FakeNorish()
    assert n.search_recipes("pa") == [{"slug": "r1", "name": "Pasta"}]
    assert n.posts[0][1]["search"] == "pa"
    merged = core.aggregate(core.collect_ingredients(n, ["r1"], "all"))
    assert merged["spaghetti"]["amounts"] == {"g": 900.0}       # 0.5 kg + 400 g


def test_mealplan_filters_dates_and_dedupes():
    out = FakeNorish().mealplan_recipes(date(2026, 11, 1), date(2026, 11, 7))
    assert out == [{"slug": "r1", "name": "Pasta"}]


def test_make_source_prefers_norish(monkeypatch):
    monkeypatch.setenv("MEALIE_URL", "http://m")
    monkeypatch.setenv("MEALIE_TOKEN", "t")
    assert isinstance(core.make_source(), core.Mealie)
    monkeypatch.setenv("NORISH_URL", "http://n")
    monkeypatch.setenv("NORISH_TOKEN", "k")
    assert isinstance(core.make_source(), core.Norish)
    for v in ("MEALIE_URL", "MEALIE_TOKEN", "NORISH_URL", "NORISH_TOKEN"):
        monkeypatch.delenv(v)
    assert core.make_source() is None


def test_norish_unit_ids_are_understood():
    assert [core.normalise_unit(u) for u in ("gram", "kilogram", "milliliter", "centiliter", "liter", "piece")] \
        == ["g", "kg", "ml", "cl", "l", ""]
