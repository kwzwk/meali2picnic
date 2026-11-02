# meali2picnic

A small self-hosted web app: pick recipes from [Mealie](https://mealie.io) or
[Norish](https://github.com/norish-recipes/norish), review the matching
[Picnic](https://picnic.app) products, and fill your Picnic cart in one go.
It **never checks out**. You review the cart and order in the Picnic app.

1. Log in to Picnic (SMS code if asked)
2. Tick recipes, and/or choose a Mealie shopping list / Norish grocery list or the next N days of your meal plan
3. Check the matched products and quantities, change anything, then "Add to Picnic cart"

Duplicate ingredients across recipes are merged, amounts are converted to g/ml and rounded up
to whole packs (900 g spaghetti with 500 g packs gives 2). When the units can't be matched to
a pack (5 onions vs. a 1 kg bag) it proposes 1 and flags the line. Your product choices are
remembered per ingredient. Salt, pepper and water are skipped.

## Run with Docker

```bash
cp .env.example .env      # fill in MEALIE_* or NORISH_*, and APP_PASSWORD
docker compose up -d --build
```

Open http://localhost:8080.

| Variable | Meaning |
|---|---|
| `MEALIE_URL` | Your Mealie base URL. Must be reachable from the container (same compose network: `http://mealie:9000`) |
| `MEALIE_TOKEN` | Mealie: user profile, API Tokens |
| `NORISH_URL` | Use Norish instead of Mealie: your Norish base URL (e.g. `http://norish:3000`). If set, it wins over Mealie |
| `NORISH_TOKEN` | Norish: Settings, User, API keys |
| `PICNIC_COUNTRY` | `DE` (default), `NL` or `FR` |
| `TRANSLATE_INGREDIENTS` | `en-de` (default) translates ingredient names for the Picnic search, `off` disables it |
| `AMAZON_DOMAIN` | Amazon store for pantry items, `amazon.de` (default), `amazon.nl`, ... or `off` to hide it |
| `AMAZON_ASSOCIATE_TAG` | Optional. Only needed if Amazon refuses the cart link without one (see below) |
| `APP_USER` / `APP_PASSWORD` | HTTP Basic auth for the whole app. **Set a password** unless it is only reachable from your own machine |

`./data` is mounted at `/data` and holds the Picnic session token and your remembered product
choices. Anyone who can open the app can use your Picnic session, so keep the port off the
internet or put it behind a reverse proxy with HTTPS. Extra ingredients to ignore can be added to
`data/skip.txt`, one per line.

## Norish

Set `NORISH_URL` and `NORISH_TOKEN` instead of the Mealie variables. Everything else works the same:

- **Grocery list:** Norish has one grocery list. The dropdown offers "All groceries" plus one entry
  per Norish store, so if you assign items to a store called "Picnic" you can send only those.
  Items you ticked off in Norish are skipped.
- **Recipes:** search and tick recipes as with Mealie (the CLI's `--recipe` takes the Norish recipe id).
- **Meal plan:** the next N days, taken from Norish's "this week" and "this month" views, so a range
  that runs past the end of both is cut short.
- Norish allows 500 API requests an hour per key, plenty for this.

## Amazon (pantry items)

Fresh food goes to Picnic, but pantry, drugstore and household items can go to an Amazon cart.
Amazon has no API that lets this app search products or fill your cart, so it works like this:

1. Choose what to buy as usual and press **Find Picnic products**. Every item you have matched to an
   Amazon product before goes to the Amazon table automatically, the rest to Picnic. To move an item,
   press "buy on Amazon instead" under it (or "back to Picnic next time" in the Amazon table).
   **Send to Amazon instead** puts everything in the Amazon table and works without a Picnic login.
2. For each item without a product yet, press **Find on Amazon**, open the product you want and paste
   its link (or the ASIN) into the row. The choice is remembered in `data/amazon.json`, so next time the
   item is ready straight away.
3. Press **Open Amazon cart**. Amazon opens with all matched items and quantities and asks you to confirm
   adding them to your cart. Nothing is ordered.

Piece counts are taken as the quantity (3 cans gives 3), anything weighed or measured starts at 1 pack.
Short links like `amzn.eu/d/...` don't contain the product id; open them first and copy the full link.

This uses Amazon's [Add-to-Cart form](https://webservices.amazon.com/paapi5/documentation/add-to-cart-form.html)
link. Amazon documents it for affiliates; if it ever refuses a link without an affiliate tag, set
`AMAZON_ASSOCIATE_TAG` to your own Associates tag.

## Translation (English recipes, German Picnic)

Ingredient names are translated from English to German **only for the Picnic search**. Your
Mealie recipes are not changed. "red onion" searches for "rote Zwiebel", "large fresh basil leaves"
for "Basilikum". The built-in dictionary covers about 300 common groceries and **learns from you**:

- Every ingredient name the dictionary doesn't know is searched as written and collected under
  **"Translations to review"** at the bottom of the page. Names that found no Picnic product are listed first.
- For each one, type the German name and press Save, or press **Keep as is** if it is already fine
  (for example a name that is already German). "Keep as is everything that already finds products"
  clears the harmless ones in one click.
- In the review table, if a match is off, type a better search term under the product and press Enter.
  When that finds the right thing, "Remember as translation" saves it to the dictionary.
- Saved entries take effect immediately, also for similar names ("red gochujang" uses "gochujang").
- The dictionary is the plain text file `data/translations.txt`, one `english = deutsch` per line
  (`#` for comments). You can edit it by hand too, and your entries win over the built-in list:
  ```
  sumac = Sumach
  onion = Gemüsezwiebel
  ```
- Set `TRANSLATE_INGREDIENTS=off` to turn translation off.
- Your product choices are remembered per original ingredient name, so a corrected match is reused.

## Command line

The same logic is available without the web UI:

```bash
pip install -r requirements.txt
# with Norish, use NORISH_URL=... NORISH_TOKEN=... instead of the MEALIE_* variables
MEALIE_URL=... MEALIE_TOKEN=... PICNIC_USER=... PICNIC_PASSWORD=... \
  python cli.py --recipe spaghetti-bolognese --mealplan 7            # preview
  python cli.py --recipe spaghetti-bolognese --mealplan 7 --apply    # fill the cart
```

## Development

```bash
pip install -r requirements.txt pytest
python -m pytest -q                       # offline tests with mocked Mealie/Picnic
DATA_DIR=./data uvicorn app.main:app --reload --port 8080
```

## Caveats

- Uses the unofficial [`python-picnic-api2`](https://pypi.org/project/python-picnic-api2/) library.
  Picnic can change its API without notice.
- Product matching is a text search on the ingredient name, so clean Mealie food names
  ("Zwiebel", not "2 mittelgroße Zwiebeln, gewürfelt") match best. Always glance at the review step.
- Tested offline against mocked responses, not against live Mealie/Picnic/Amazon accounts.

- Needs Python 3.13 or newer (required by `python-picnic-api2`); the Docker image already uses it.
