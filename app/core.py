"""Shared logic: Mealie client, ingredient parsing/merging, Picnic pack maths."""
from __future__ import annotations

import math
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


def _name(obj) -> str:
    return ((obj or {}).get("name") or "") if isinstance(obj, dict) else ""


UNIT_ALIASES = {
    "gram": "g", "gramm": "g", "grams": "g", "g": "g",
    "kilogram": "kg", "kilo": "kg", "kg": "kg",
    "milliliter": "ml", "millilitre": "ml", "ml": "ml",
    "liter": "l", "litre": "l", "l": "l",
    "cl": "cl", "dl": "dl",
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


def collect_ingredients(mealie: Mealie, slugs=(), shopping_list: str | None = None,
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
