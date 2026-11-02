"""Web app: pick Mealie or Norish recipes -> review matched Picnic products -> fill the Picnic cart."""
from __future__ import annotations

import base64
import json
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import amazon, core, matching
from .autotranslate import make_backend
from .dictionary import Dictionary
from .picnic_session import DATA_DIR, Picnic2FAError, PicnicSession

app = FastAPI(title="meali2picnic")
STATIC = Path(__file__).parent / "static"
MAPPING_FILE = DATA_DIR / "mappings.json"   # ingredient name -> Picnic product id
AMAZON_FILE = DATA_DIR / "amazon.json"      # ingredient name -> {"asin", "title"}
TERM_FILE = DATA_DIR / "term_choices.json"  # German search term -> Picnic product id
SKIP_FILE = DATA_DIR / "skip.txt"           # extra ingredients to ignore, one per line
picnic = PicnicSession()

APP_USER = os.environ.get("APP_USER", "admin")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """Optional HTTP Basic auth for the whole app. Strongly recommended if reachable beyond localhost."""
    if APP_PASSWORD and request.url.path != "/health":
        ok = False
        header = request.headers.get("authorization", "")
        if header.lower().startswith("basic "):
            try:
                user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
                ok = secrets.compare_digest(user, APP_USER) & secrets.compare_digest(pw, APP_PASSWORD)
            except Exception:
                ok = False
        if not ok:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="meali2picnic"'})
    return await call_next(request)


def mealie():
    """The configured recipe source: Norish (NORISH_URL/NORISH_TOKEN) or Mealie (MEALIE_URL/MEALIE_TOKEN)."""
    src = core.make_source()
    if src is None:
        raise HTTPException(503, "Set NORISH_URL and NORISH_TOKEN, or MEALIE_URL and MEALIE_TOKEN.")
    return src


def need_picnic():
    if not picnic.logged_in():
        raise HTTPException(401, "Not logged in to Picnic.")
    return picnic.api


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


TRANSLATE_ON = os.environ.get("TRANSLATE_INGREDIENTS", "en-de").lower() == "en-de"
dictionary = Dictionary(DATA_DIR, make_backend() if TRANSLATE_ON else None)


def search_term(name: str) -> tuple[str, bool]:
    """(the word we search Picnic for, whether the dictionary knew the name)."""
    return dictionary.translate(name) if TRANSLATE_ON else (name, True)


def skip_set() -> set[str]:
    extra = set()
    if SKIP_FILE.exists():
        extra = {l.strip().lower() for l in SKIP_FILE.read_text().splitlines() if l.strip()}
    return core.DEFAULT_SKIP | extra


# ------------------------------------------------------------------ API


@app.get("/api/status")
def status():
    return {
        "mealie_configured": core.source_name() is not None,   # kept for older front ends
        "source_configured": core.source_name() is not None,
        "source": core.source_name(),
        "picnic_logged_in": picnic.logged_in(),
        "default_country": os.environ.get("PICNIC_COUNTRY", "DE"),
        "amazon_domain": amazon.domain() or None,
        "auto_translate": dictionary.auto.backend.name if dictionary.auto.enabled else None,
    }


class LoginBody(BaseModel):
    username: str
    password: str
    country: str = "DE"


class CodeBody(BaseModel):
    code: str


@app.post("/api/picnic/login")
def picnic_login(body: LoginBody):
    try:
        return picnic.login(body.username, body.password, body.country.upper())
    except Exception as e:
        raise HTTPException(400, f"Picnic login failed: {e}")


@app.post("/api/picnic/2fa")
def picnic_2fa(body: CodeBody):
    try:
        warning = picnic.verify_2fa(body.code)
    except Picnic2FAError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "warning": warning}


@app.post("/api/picnic/logout")
def picnic_logout():
    picnic.logout()
    return {"ok": True}


@app.get("/api/recipes")
def recipes(q: str = ""):
    items = mealie().search_recipes(q)
    return [{"slug": r["slug"], "name": r["name"]} for r in items]


@app.get("/api/shopping-lists")
def shopping_lists():
    return [{"id": s["id"], "name": s.get("name") or s["id"]} for s in mealie().shopping_lists()]


def build_row(api, name: str, amounts: dict, hits, remembered: str | None, best: float | None = None) -> dict:
    cands = [{"id": h.id, "name": h.name, "price": h.display_price, "unit_quantity": h.unit_quantity} for h in hits]
    selected = remembered
    if selected and not any(c["id"] == selected for c in cands):
        try:  # remembered product that isn't in today's top hits
            art = api.get_article(selected)
            if art:
                cands.insert(0, {"id": art.id, "name": art.name, "price": None, "unit_quantity": None})
        except Exception:
            selected = None
    if not selected and cands:
        selected = cands[0]["id"]
    chosen = next((c for c in cands if c["id"] == selected), None)
    count, note = core.packs_needed(amounts, chosen["unit_quantity"] if chosen else None)
    if chosen and selected != remembered and best is not None and best < matching.WEAK:
        note = "; ".join(x for x in ("unsure match, please check", note) if x)
    return {
        "ingredient": name,
        "needed": core.describe_amounts(amounts),
        "amounts": amounts,
        "candidates": cands,
        "selected": selected,
        "count": count if chosen else 0,
        "note": note if chosen else "no Picnic match",
    }


class ResearchBody(BaseModel):
    ingredient: str
    term: str
    amounts: dict[str, float] = {}


@app.post("/api/research")
def research(body: ResearchBody):
    """Search Picnic again with a term you typed for one ingredient."""
    api = need_picnic()
    hits, best = matching.rank(body.term, list(api.search(body.term).items), body.amounts)
    row = build_row(api, body.ingredient, body.amounts, hits, None, best)
    row["search_term"] = body.term
    return row


class TranslationBody(BaseModel):
    english: str
    german: str = ""


@app.get("/api/translations")
def translations():
    """Ingredient names the dictionary doesn't know yet."""
    return {"untranslated": dictionary.untranslated()}


@app.post("/api/translations")
def save_translation(body: TranslationBody):
    """Teach the dictionary. An empty translation means 'keep this name as it is'."""
    try:
        dictionary.set(body.english, body.german)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except OSError as e:
        raise HTTPException(500, f"Could not save the dictionary ({e}). Make {DATA_DIR} writable for uid 10001.")
    return {"untranslated": dictionary.untranslated()}


@app.post("/api/translations/keep-found")
def keep_found():
    """Mark every untranslated name that already finds Picnic products as fine as written."""
    try:
        n = dictionary.keep_found()
    except OSError as e:
        raise HTTPException(500, f"Could not save the dictionary ({e}).")
    return {"kept": n, "untranslated": dictionary.untranslated()}


class PlanBody(BaseModel):
    recipes: list[str] = []
    shopping_list: str | None = None
    mealplan_days: int | None = None


@app.post("/api/plan")
def plan(body: PlanBody):
    """Match every ingredient to Picnic products. Nothing is added to the cart yet."""
    if not (body.recipes or body.shopping_list or body.mealplan_days):
        raise HTTPException(400, "Choose at least one recipe, a shopping list or a meal-plan range.")
    api = need_picnic()
    try:
        ings = core.collect_ingredients(mealie(), body.recipes, body.shopping_list, body.mealplan_days)
    except KeyError as e:
        raise HTTPException(404, str(e))
    skip = skip_set()
    merged = {k: v for k, v in core.aggregate(ings).items() if k not in skip}
    mappings = load_json(MAPPING_FILE)
    amazon_maps = load_json(AMAZON_FILE) if amazon.enabled() else {}
    term_choices = load_json(TERM_FILE)
    terms = dictionary.translate_all(list(merged)) if TRANSLATE_ON else {n: (n, "off") for n in merged}

    rows, amazon_rows = [], []
    for name, entry in merged.items():
        if name in amazon_maps:               # already matched to an Amazon product: goes there
            row = amazon_row(name, entry["amounts"], amazon_maps[name])
            row["from"] = entry["displays"]
            amazon_rows.append(row)
            continue
        term, source = terms[name]
        hits, term = matching.search(api, term, name)   # falls back to the product noun, then the original
        if source in ("auto", "none"):
            dictionary.note_unknown(name, bool(hits))
        hits, best = matching.rank(term, hits, entry["amounts"])
        pid, why = matching.remembered(mappings, term_choices, name, term)
        row = build_row(api, name, entry["amounts"], hits, pid, best)
        row.update({"search_term": term, "translated": source != "none", "translation": source,
                    "from": entry["displays"], "remembered": bool(pid), "remembered_by": why,
                    "amazon_search_url": amazon.search_url(term) if amazon.enabled() else None})
        rows.append(row)
    return {"rows": rows, "amazon_rows": amazon_rows}


class CartLine(BaseModel):
    ingredient: str
    product_id: str
    count: int
    search_term: str = ""


class CartBody(BaseModel):
    lines: list[CartLine]
    clear_first: bool = False


@app.post("/api/cart")
def add_to_cart(body: CartBody):
    """Add the reviewed lines to the Picnic cart. Does not check out."""
    api = need_picnic()
    if body.clear_first:
        api.clear_cart()
    mappings, term_choices = load_json(MAPPING_FILE), load_json(TERM_FILE)
    for line in body.lines:
        if line.count > 0:
            api.add_product(line.product_id, line.count)
            mappings[line.ingredient] = line.product_id
            if line.search_term.strip():
                term_choices[line.search_term.strip().lower()] = line.product_id
    warning = None
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        MAPPING_FILE.write_text(json.dumps(mappings, indent=2, ensure_ascii=False))
        TERM_FILE.write_text(json.dumps(term_choices, indent=2, ensure_ascii=False))
    except OSError as e:  # the cart is already filled; only remembering choices failed
        warning = f"Added to cart, but product choices could not be saved ({e})."
    cart = api.get_cart()
    return {"total_count": cart.total_count, "total_price": (cart.total_price or 0) / 100, "warning": warning}


# ------------------------------------------------------------------ Amazon (pantry items)


def need_amazon():
    if not amazon.enabled():
        raise HTTPException(404, "Amazon is turned off (AMAZON_DOMAIN=off).")


def save_json(path: Path, data: dict) -> str | None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        return None
    except OSError as e:
        return f"Could not save to {path} ({e}). Make {DATA_DIR} writable for uid 10001."


def amazon_row(name: str, amounts: dict, mapping: dict | None) -> dict:
    term, _ = search_term(name)
    asin = (mapping or {}).get("asin")
    return {
        "ingredient": name,
        "needed": core.describe_amounts(amounts),
        "amounts": amounts,
        "asin": asin,
        "title": (mapping or {}).get("title") or "",
        "product_url": amazon.product_url(asin) if asin else None,
        "search_url": amazon.search_url(term),
        "count": amazon.default_count(amounts) if asin else 0,
    }


@app.post("/api/amazon/plan")
def amazon_plan(body: PlanBody):
    """List the chosen items with their remembered Amazon product. Needs no Picnic login."""
    need_amazon()
    if not (body.recipes or body.shopping_list or body.mealplan_days):
        raise HTTPException(400, "Choose at least one recipe, a shopping list or a meal-plan range.")
    try:
        ings = core.collect_ingredients(mealie(), body.recipes, body.shopping_list, body.mealplan_days)
    except KeyError as e:
        raise HTTPException(404, str(e))
    skip = skip_set()
    mappings = load_json(AMAZON_FILE)
    rows = []
    for name, entry in core.aggregate(ings).items():
        if name in skip:
            continue
        row = amazon_row(name, entry["amounts"], mappings.get(name))
        row["from"] = entry["displays"]
        rows.append(row)
    return {"rows": rows, "domain": amazon.domain()}


class AmazonMapBody(BaseModel):
    ingredient: str
    product: str = ""          # ASIN or Amazon product link; empty forgets the mapping
    title: str = ""
    amounts: dict[str, float] = {}


@app.post("/api/amazon/map")
def amazon_map(body: AmazonMapBody):
    """Remember which Amazon product to buy for an ingredient."""
    need_amazon()
    mappings = load_json(AMAZON_FILE)
    name = body.ingredient.strip().lower()
    if body.product.strip():
        asin = amazon.extract_asin(body.product)
        if not asin:
            raise HTTPException(400, "That is not an ASIN or an Amazon product link (it should contain /dp/…).")
        mappings[name] = {"asin": asin, "title": body.title.strip()[:120]}
    else:
        mappings.pop(name, None)
    error = save_json(AMAZON_FILE, mappings)
    if error:
        raise HTTPException(500, error)
    return amazon_row(name, body.amounts, mappings.get(name))


class AmazonLine(BaseModel):
    asin: str
    count: int


class AmazonCartBody(BaseModel):
    lines: list[AmazonLine]


@app.post("/api/amazon/cart")
def amazon_cart(body: AmazonCartBody):
    """Build the Amazon add-to-cart link. Opening it lets you confirm the items on Amazon."""
    need_amazon()
    lines = []
    for line in body.lines:
        asin = amazon.extract_asin(line.asin)
        if not asin:
            raise HTTPException(400, f"Invalid ASIN: {line.asin}")
        if line.count > 0:
            lines.append((asin, min(line.count, 99)))
    if not lines:
        raise HTTPException(400, "Nothing to add.")
    return {"url": amazon.cart_url(lines), "items": len(lines)}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
