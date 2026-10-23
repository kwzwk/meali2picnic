# meali2picnic

A small self-hosted web app: pick recipes from [Mealie](https://mealie.io), review the matching
[Picnic](https://picnic.app) products, and fill your Picnic cart in one go.
It **never checks out**. You review the cart and order in the Picnic app.

1. Log in to Picnic (SMS code if asked)
2. Tick recipes, and/or choose a Mealie shopping list or the next N days of your meal plan
3. Check the matched products and quantities, change anything, then "Add to Picnic cart"

Duplicate ingredients across recipes are merged, amounts are converted to g/ml and rounded up
to whole packs (900 g spaghetti with 500 g packs gives 2). When the units can't be matched to
a pack (5 onions vs. a 1 kg bag) it proposes 1 and flags the line. Your product choices are
remembered per ingredient. Salt, pepper and water are skipped.

## Run with Docker

```bash
cp .env.example .env      # fill in MEALIE_URL, MEALIE_TOKEN, and APP_PASSWORD
docker compose up -d --build
```

Open http://localhost:8080.

| Variable | Meaning |
|---|---|
| `MEALIE_URL` | Your Mealie base URL. Must be reachable from the container (same compose network: `http://mealie:9000`) |
| `MEALIE_TOKEN` | Mealie: user profile, API Tokens |
| `PICNIC_COUNTRY` | `DE` (default), `NL` or `FR` |
| `TRANSLATE_INGREDIENTS` | `en-de` (default) translates ingredient names for the Picnic search, `off` disables it |
| `APP_USER` / `APP_PASSWORD` | HTTP Basic auth for the whole app. **Set a password** unless it is only reachable from your own machine |

`./data` is mounted at `/data` and holds the Picnic session token and your remembered product
choices. Anyone who can open the app can use your Picnic session, so keep the port off the
internet or put it behind a reverse proxy with HTTPS. Extra ingredients to ignore can be added to
`data/skip.txt`, one per line.

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
- Tested offline against mocked responses, not against live Mealie/Picnic accounts.

- Needs Python 3.13 or newer (required by `python-picnic-api2`); the Docker image already uses it.
