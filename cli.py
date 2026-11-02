#!/usr/bin/env python3
"""Command-line version: Mealie or Norish recipes / meal plan / shopping list -> Picnic cart.

Preview by default; --apply really fills the cart. Never checks out.
Env: NORISH_URL + NORISH_TOKEN or MEALIE_URL + MEALIE_TOKEN, PICNIC_USER, PICNIC_PASSWORD (first login), PICNIC_COUNTRY (DE).
"""
import argparse
import os
import sys
from pathlib import Path

from python_picnic_api2 import PicnicAPI
from python_picnic_api2.session import Picnic2FARequired

from app import core, matching
from app.autotranslate import make_backend
from app.dictionary import Dictionary

TOKEN_FILE = Path(os.environ.get("DATA_DIR", Path.home() / ".config" / "meali2picnic")) / "picnic_token"


def picnic_client(country: str) -> PicnicAPI:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    if TOKEN_FILE.exists():
        api = PicnicAPI(country_code=country, auth_token=TOKEN_FILE.read_text().strip())
        try:
            api.get_user()
            return api
        except Exception:
            print("Stored Picnic session expired, logging in again.", file=sys.stderr)
    user, pw = os.environ.get("PICNIC_USER"), os.environ.get("PICNIC_PASSWORD")
    if not (user and pw):
        raise SystemExit("Set PICNIC_USER and PICNIC_PASSWORD for the first login.")
    api = PicnicAPI(country_code=country)
    try:
        api.login(user, pw)
    except Picnic2FARequired:
        api.generate_2fa_code("SMS")
        api.verify_2fa_code(input("Picnic SMS code: ").strip())
    TOKEN_FILE.write_text(api.session.auth_token or "")
    TOKEN_FILE.chmod(0o600)
    return api


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recipe", action="append", default=[], metavar="SLUG", help="Mealie recipe slug or Norish recipe id (repeatable)")
    ap.add_argument("--mealplan", type=int, metavar="DAYS", help="meal plan for the next N days")
    ap.add_argument("--shopping-list", metavar="NAME_OR_ID",
                    help="Mealie shopping list; for Norish 'all' or a store name")
    ap.add_argument("--apply", action="store_true", help="really add to the Picnic cart")
    ap.add_argument("--country", default=os.environ.get("PICNIC_COUNTRY", "DE"))
    args = ap.parse_args(argv)
    if not (args.recipe or args.mealplan or args.shopping_list):
        ap.error("pick a source: --recipe, --mealplan or --shopping-list")
    mealie = core.make_source()
    if mealie is None:
        raise SystemExit("Set NORISH_URL and NORISH_TOKEN, or MEALIE_URL and MEALIE_TOKEN.")
    merged = core.aggregate(core.collect_ingredients(mealie, args.recipe, args.shopping_list, args.mealplan))
    merged = {k: v for k, v in merged.items() if k not in core.DEFAULT_SKIP}
    api = picnic_client(args.country)

    translate_on = os.environ.get("TRANSLATE_INGREDIENTS", "en-de").lower() == "en-de"
    dictionary = Dictionary(TOKEN_FILE.parent, make_backend() if translate_on else None)
    terms = dictionary.translate_all(list(merged)) if translate_on else {n: (n, "off") for n in merged}
    plan = []
    for name, entry in merged.items():
        hits, term = matching.search(api, terms[name][0], name)
        hits, best = matching.rank(term, hits, entry["amounts"])
        if not hits:
            print(f"  no Picnic match: {name} (searched {term})")
            continue
        count, note = core.packs_needed(entry["amounts"], hits[0].unit_quantity)
        if best is not None and best < matching.WEAK:
            note = "; ".join(x for x in ("unsure match", note) if x)
        plan.append((hits[0], count))
        print(f"  {count} x {hits[0].name}  <- {name} ({core.describe_amounts(entry['amounts'])})" + (f"  [{note}]" if note else ""))
    if not args.apply:
        print("\nPreview only. Add --apply to fill the cart.")
        return 0
    for p, count in plan:
        api.add_product(p.id, count)
    cart = api.get_cart()
    print(f"\nCart: {cart.total_count} items, about {(cart.total_price or 0) / 100:.2f} EUR. Order in the Picnic app.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
