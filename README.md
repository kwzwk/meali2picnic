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
| `ANTHROPIC_API_KEY` | Optional. Claude translates names the dictionary doesn't know (see below) |
| `DEEPL_API_KEY` | Optional. DeepL does it instead (the free API key works) |
| `AUTO_TRANSLATE` | `auto` (default: Claude if its key is set, else DeepL, else nothing), `claude`, `deepl` or `off` |
| `CLAUDE_MODEL` | Model for the Claude translation, default `claude-opus-5-5` |
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

### Automatic translation

Set `ANTHROPIC_API_KEY` (or `DEEPL_API_KEY`) and names the dictionary doesn't know are translated
automatically instead of being searched in English:

- **Claude** is asked for the term you would type into Picnic's search, not a literal translation:
  "chicken thighs, boneless" becomes "Hähnchenschenkel", "heavy cream" becomes "Schlagsahne".
  All unknown names of one shopping run go in a single request, at low effort, so it costs a fraction of a cent.
- **DeepL** translates the cleaned-up name literally ("large fresh basil leaves" -> "basil" -> "Basilikum").
- Each name is translated once and cached in `data/auto_translations.json`. Your dictionary
  (`translations.txt`) and the built-in list always come first.
- Auto-translated lines say so in the review table and appear under "Translations to review" with the
  suggestion filled in: Save keeps it (or your correction) in your dictionary. "Confirm everything that
  already finds products" accepts all suggestions that work in one click.
- If the service is down or the key is wrong, the name is simply searched as written, as before.

## Product matching

- Picnic's top 10 results are re-ranked. German compound words are read the German way: for "Tomaten",
  "Rispentomaten" is a good match and "Tomatenmark" a poor one. Processed forms you didn't ask for
  (Mark, Saft, Pulver, Soße, Chips, Kekse, ...) move down, and a pack size that fits the amount you need
  with little left over moves up. Picnic's own order breaks ties.
- If a multi-word term finds nothing, the last word (the product noun) is tried, then the original name:
  "geräuchertes Paprikapulver" falls back to "Paprikapulver".
- Lines where even the best product is a poor fit are flagged "unsure match, please check".
- Your product choices are remembered per ingredient name (also for small spelling differences such as
  "Red onions" and "red onion") **and per search term**, so the onions you picked for "onion" are also used for
  "onions, diced" or "Zwiebel". Remembered choices are stored in `data/mappings.json` and `data/term_choices.json`.

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
- Product matching starts from a text search on the ingredient name, so clean Mealie food names
  ("Zwiebel", not "2 mittelgroße Zwiebeln, gewürfelt") match best. Always glance at the review step.
- Tested offline against mocked responses, not against live Mealie/Picnic accounts.

- Needs Python 3.13 or newer (required by `python-picnic-api2`); the Docker image already uses it.
