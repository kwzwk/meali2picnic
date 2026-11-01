"""Shared logic: Mealie/Norish clients, ingredient parsing/merging, Picnic pack maths."""
from __future__ import annotations

import math
import os
import re
from collections import OrderedDict
from datetime import date, timedelta

import requests

DEFAULT_SKIP = {"salt", "salz", "pepper", "pfeffer", "water", "wasser", "zout", "peper"}

# ---------------------------------------------------------------- Mealie


class Mealie:
    def __init__(self, base_url: str, token: str):
        self.base = base_url.rstrip("/") + "/api"
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"

    def _get(self, path: str, **params):
        r = self.s.get(self.base + path, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def recipe(self, slug: str) -> dict:
        return self._get(f"/recipes/{slug}")

    def search_recipes(self, text: str = "", limit: int = 30) -> list[dict]:
        params = {"perPage": limit, "orderBy": "name", "orderDirection": "asc"}
        if text:
            params["search"] = text
        return self._get("/recipes", **params).get("items", [])

    def mealplan_recipes(self, start: date, end: date) -> list[dict]:
        data = self._get("/households/mealplans", start_date=start.isoformat(),
                         end_date=end.isoformat(), perPage=200)
        return [e["recipe"] for e in data.get("items", []) if e.get("recipe")]

    def shopping_lists(self) -> list[dict]:
        return self._get("/households/shopping/lists", perPage=100).get("items", [])

    def shopping_list(self, name_or_id: str) -> dict:
        for sl in self.shopping_lists():
            if name_or_id in (sl["id"], sl.get("name")):
                return self._get(f"/households/shopping/lists/{sl['id']}")
        raise KeyError(f"No Mealie shopping list '{name_or_id}'")


# ---------------------------------------------------------------- Norish


class Norish:
    """Norish (https://github.com/norish-recipes/norish) behind the same interface as Mealie.

    Recipes, groceries and meal-plan entries are converted to Mealie's field names, so the rest
    of the app does not care which one it talks to. A recipe's "slug" is its Norish id.
    Norish has one grocery list; it is offered as "All groceries" plus one list per store.
    """

    ALL = "all"

    def __init__(self, base_url: str, token: str):
        self.base = base_url.rstrip("/") + "/api/v1"
        self.s = requests.Session()
        self.s.headers["x-api-key"] = token

    def _get(self, path: str, **params):
        r = self.s.get(self.base + path, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict):
        r = self.s.post(self.base + path, json=body, timeout=30)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _ingredient(ing: dict) -> dict:
        unit = ing.get("unit") or ""
        return {"food": {"name": ing.get("ingredientName") or ""}, "unit": {"name": unit},
                "quantity": ing.get("amount"),
                "display": " ".join(str(x) for x in (_fmt(ing.get("amount")), unit, ing.get("ingredientName")) if x)}

    def recipe(self, slug: str) -> dict:
        r = self._get(f"/recipes/{slug}")
        return {"slug": r["id"], "name": r.get("name"),
                "recipeIngredient": [self._ingredient(i) for i in r.get("recipeIngredients", [])]}

    def search_recipes(self, text: str = "", limit: int = 30) -> list[dict]:
        body = {"limit": limit, "sortMode": "titleAsc"}
        if text:
            body["search"] = text
        return [{"slug": r["id"], "name": r["name"]} for r in self._post("/recipes/search", body).get("recipes", [])]

    def mealplan_recipes(self, start: date, end: date) -> list[dict]:
        # Norish only offers today / this week / this month, so take week + month and filter.
        seen, out = set(), []
        for span in ("week", "month"):
            for e in self._get(f"/planned-recipes/{span}"):
                if e["id"] in seen or not (start.isoformat() <= (e.get("date") or "")[:10] <= end.isoformat()):
                    continue
                seen.add(e["id"])
                out.append({"slug": e["recipeId"], "name": e.get("recipeName")})
        return out

    def shopping_lists(self) -> list[dict]:
        stores = [{"id": s["id"], "name": s.get("name") or s["id"]} for s in self._get("/stores")]
        return [{"id": self.ALL, "name": "All groceries"}] + stores

    def shopping_list(self, name_or_id: str) -> dict:
        lists = self.shopping_lists()
        match = next((sl for sl in lists if name_or_id in (sl["id"], sl["name"])), None)
        if not match:
            raise KeyError(f"No Norish grocery list or store '{name_or_id}'")
        items = []
        for g in self._get("/groceries"):
            if match["id"] != self.ALL and g.get("storeId") != match["id"]:
                continue
            unit = g.get("unit") or ""
            # Same rule as Norish itself: without a unit, purchaseAmount is the number to buy.
            qty = g.get("amount") if unit else (g.get("purchaseAmount") or g.get("amount"))
            items.append({"checked": g.get("isDone"), "food": {"name": g.get("name") or ""},
                          "unit": {"name": unit}, "quantity": qty,
                          "display": " ".join(str(x) for x in (_fmt(qty), unit, g.get("name")) if x)})
        return {"id": match["id"], "name": match["name"], "listItems": items}


def _fmt(qty) -> str:
    return f"{qty:g}" if isinstance(qty, (int, float)) and qty else ""


def make_source():
    """Pick Mealie or Norish from the environment. Returns None if neither is configured."""
    if os.environ.get("NORISH_URL") and os.environ.get("NORISH_TOKEN"):
        return Norish(os.environ["NORISH_URL"], os.environ["NORISH_TOKEN"])
    if os.environ.get("MEALIE_URL") and os.environ.get("MEALIE_TOKEN"):
        return Mealie(os.environ["MEALIE_URL"], os.environ["MEALIE_TOKEN"])
    return None


def source_name() -> str | None:
    s = make_source()
    return None if s is None else type(s).__name__


def _name(obj) -> str:
    return ((obj or {}).get("name") or "") if isinstance(obj, dict) else ""


UNIT_ALIASES = {
    "gram": "g", "gramm": "g", "grams": "g", "g": "g",
    "kilogram": "kg", "kilograms": "kg", "kilo": "kg", "kg": "kg",
    "milliliter": "ml", "milliliters": "ml", "millilitre": "ml", "ml": "ml",
    "liter": "l", "liters": "l", "litre": "l", "l": "l",
    "centiliter": "cl", "cl": "cl", "deciliter": "dl", "dl": "dl",   # Norish uses the long ids
}
TO_BASE = {"kg": ("g", 1000), "l": ("ml", 1000), "cl": ("ml", 10), "dl": ("ml", 100)}


def normalise_unit(u: str) -> str:
    u = u.strip().lower().rstrip(".")
    return UNIT_ALIASES.get(u, "")  # anything else (EL, TL, Stück, ...) counts as "pieces"


def to_base(qty: float | None, unit: str) -> tuple[float | None, str]:
    if qty is not None and unit in TO_BASE:
        base, factor = TO_BASE[unit]
        return qty * factor, base
    return qty, unit


def parse_ingredient(item: dict) -> dict | None:
    """Works for recipe ingredients and shopping-list items (same field names)."""
    if item.get("checked"):
        return None
    food = _name(item.get("food")).strip()
    note = (item.get("note") or "").strip()
    display = (item.get("display") or item.get("originalText") or "").strip()
    name = food or note or display
    if not name:
        return None
    qty = item.get("quantity")
    return {
        "name": name.lower(),
        "qty": float(qty) if qty not in (None, 0, "") else None,
        "unit": normalise_unit(_name(item.get("unit"))),
        "display": display or name,
    }


def aggregate(ingredients: list[dict]) -> "OrderedDict[str, dict]":
    """Merge identical ingredients across recipes, summing compatible units."""
    merged: "OrderedDict[str, dict]" = OrderedDict()
    for ing in ingredients:
        qty, unit = to_base(ing["qty"], ing["unit"])
        entry = merged.setdefault(ing["name"], {"name": ing["name"], "amounts": {}, "displays": []})
        entry["amounts"][unit] = entry["amounts"].get(unit, 0.0) + (qty if qty is not None else 0.0)
        entry["displays"].append(ing["display"])
    return merged


def collect_ingredients(mealie: "Mealie | Norish", slugs=(), shopping_list: str | None = None,
                        mealplan_days: int | None = None) -> list[dict]:
    recipes = [mealie.recipe(s) for s in slugs]
    if mealplan_days:
        today = date.today()
        for r in mealie.mealplan_recipes(today, today + timedelta(days=mealplan_days - 1)):
            recipes.append(r if "recipeIngredient" in r else mealie.recipe(r["slug"]))
    out = []
    for r in recipes:
        out += [p for p in map(parse_ingredient, r.get("recipeIngredient", [])) if p]
    if shopping_list:
        out += [p for p in map(parse_ingredient, mealie.shopping_list(shopping_list).get("listItems", [])) if p]
    return out


def describe_amounts(amounts: dict[str, float]) -> str:
    parts = []
    for unit, qty in amounts.items():
        if qty <= 0:
            continue
        parts.append(f"{qty:g} {unit}".strip() if unit else f"{qty:g}x")
    return " + ".join(parts) or "no amount"


# ---------------------------------------------------------------- Picnic pack maths

PACK_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|g|ml|cl|l)\b", re.I)
COUNT_RE = re.compile(r"(\d+)\s*(?:x\s*)?(?:stuks|stuk|st|stück|pcs|pieces|pièces)?", re.I)


def pack_size(unit_quantity: str | None) -> tuple[float | None, str]:
    """'500 g' -> (500, 'g'); '1 kg' -> (1000, 'g'); '6 stuks' -> (6, '')."""
    if not unit_quantity:
        return None, ""
    m = PACK_RE.search(unit_quantity)
    if m:
        return to_base(float(m.group(1).replace(",", ".")), m.group(2).lower())
    m = COUNT_RE.search(unit_quantity)
    return (float(m.group(1)), "") if m else (None, "")


def packs_needed(amounts: dict[str, float], unit_quantity: str | None) -> tuple[int, str]:
    """How many packs to buy. Returns (count, note); note is set when it's a guess."""
    if not any(v > 0 for v in amounts.values()):
        return 1, "no amount in recipe, bought 1"
    psize, punit = pack_size(unit_quantity)
    if psize and punit in amounts and amounts[punit] > 0:
        return max(1, math.ceil(amounts[punit] / psize - 1e-9)), ""
    if psize and punit == "" and amounts.get("", 0) > 0 and psize > 1:
        return max(1, math.ceil(amounts[""] / psize - 1e-9)), ""
    if punit == "" and amounts.get("", 0) > 0 and (psize in (None, 1)):
        return max(1, math.ceil(amounts[""])), ""
    return 1, "units don't match the pack size, check the amount"
