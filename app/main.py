"""Web app: pick Mealie recipes -> review matched Picnic products -> fill the Picnic cart."""
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

from . import core
from .picnic_session import DATA_DIR, Picnic2FAError, PicnicSession

app = FastAPI(title="meali2picnic")
STATIC = Path(__file__).parent / "static"
MAPPING_FILE = DATA_DIR / "mappings.json"   # ingredient name -> Picnic product id
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


def mealie() -> core.Mealie:
    url, token = os.environ.get("MEALIE_URL"), os.environ.get("MEALIE_TOKEN")
    if not (url and token):
        raise HTTPException(503, "MEALIE_URL and MEALIE_TOKEN are not configured.")
    return core.Mealie(url, token)


def need_picnic():
    if not picnic.logged_in():
        raise HTTPException(401, "Not logged in to Picnic.")
    return picnic.api


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def skip_set() -> set[str]:
    extra = set()
    if SKIP_FILE.exists():
        extra = {l.strip().lower() for l in SKIP_FILE.read_text().splitlines() if l.strip()}
    return core.DEFAULT_SKIP | extra


# ------------------------------------------------------------------ API


@app.get("/api/status")
def status():
    return {
        "mealie_configured": bool(os.environ.get("MEALIE_URL") and os.environ.get("MEALIE_TOKEN")),
        "picnic_logged_in": picnic.logged_in(),
        "default_country": os.environ.get("PICNIC_COUNTRY", "DE"),
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

    rows = []
    for name, entry in merged.items():
        hits = api.search(name).items[:6]
        cands = [{"id": h.id, "name": h.name, "price": h.display_price, "unit_quantity": h.unit_quantity}
                 for h in hits]
        selected = mappings.get(name)
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
        count, note = core.packs_needed(entry["amounts"], chosen["unit_quantity"] if chosen else None)
        rows.append({
            "ingredient": name,
            "needed": core.describe_amounts(entry["amounts"]),
            "from": entry["displays"],
            "candidates": cands,
            "selected": selected,
            "count": count if chosen else 0,
            "note": note if chosen else "no Picnic match",
            "remembered": name in mappings,
        })
    return {"rows": rows}


class CartLine(BaseModel):
    ingredient: str
    product_id: str
    count: int


class CartBody(BaseModel):
    lines: list[CartLine]
    clear_first: bool = False


@app.post("/api/cart")
def add_to_cart(body: CartBody):
    """Add the reviewed lines to the Picnic cart. Does not check out."""
    api = need_picnic()
    if body.clear_first:
        api.clear_cart()
    mappings = load_json(MAPPING_FILE)
    for line in body.lines:
        if line.count > 0:
            api.add_product(line.product_id, line.count)
            mappings[line.ingredient] = line.product_id
    warning = None
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        MAPPING_FILE.write_text(json.dumps(mappings, indent=2, ensure_ascii=False))
    except OSError as e:  # the cart is already filled; only remembering choices failed
        warning = f"Added to cart, but product choices could not be saved ({e})."
    cart = api.get_cart()
    return {"total_count": cart.total_count, "total_price": (cart.total_price or 0) / 100, "warning": warning}


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
