"""Picking the right Picnic product for a search term.

Picnic's own order is a good start but often puts a related product first ("Tomatenmark" for
"Tomaten", "Zwiebelbaguette" for "Zwiebeln"). The ranking here re-orders Picnic's top hits by:

- how well the product name matches the term, German-style: in "Rispentomaten" the last part
  is the product (good match for "Tomaten"), in "Tomatenmark" the first part only describes it (poor);
- processed forms (Mark, Saft, Pulver, Soße, ...) that the term didn't ask for count against a product;
- Picnic's own order, as a tie-breaker;
- how well the pack size fits the amount you need (less left over is better).
"""
from __future__ import annotations

import math
import re

from . import core

SEARCH_LIMIT = 10     # Picnic hits we look at
SHOW = 8              # candidates offered in the review table
WEAK = 1.0            # best score below this: the line is flagged for a look

STOPWORDS = {"und", "mit", "in", "im", "der", "die", "das", "von", "für", "aus", "oder"}
# Words that turn a fresh/basic product into something else. Matched as whole words or compound endings.
PROCESSED = {
    "mark", "saft", "pulver", "soße", "sosse", "sauce", "suppe", "chips", "brühe", "püree", "sirup",
    "aufstrich", "pesto", "paste", "riegel", "snack", "kuchen", "brot", "baguette", "brötchen", "eis",
    "getränk", "drink", "nektar", "schorle", "gewürz", "würzmischung", "dressing", "konfitüre",
    "marmelade", "creme", "joghurt", "salat", "chutney", "ketchup", "flocken", "granulat",
    "keks", "kekse", "pudding", "likör", "schokolade", "nudeln", "reis", "wurst",
    "passiert", "getrocknet", "eingelegt", "tiefgekühlt", "tk",
}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-zäöüß]+", (text or "").lower())


def _stem(w: str) -> str:
    """Rough German plural/adjective folding: Tomaten/Tomate -> tomat, Zwiebeln -> zwiebel."""
    for _ in range(2):                                     # "geräuchertes" -> "geräucherte" -> "geräuchert"
        suf = next((s for s in ("en", "er", "es", "em", "n", "e", "s") if w.endswith(s) and len(w) - len(s) >= 4), None)
        if not suf:
            break
        w = w[: -len(suf)]
    return w


def name_score(term: str, product: str) -> float:
    """How well a product name fits the search term. Roughly: 3 per term word found as a word,
    2 as the end of a compound, minus points for missing words and unrequested processed forms."""
    t_words = [w for w in _words(term) if w not in STOPWORDS]
    p_words = _words(product)
    if not t_words or not p_words:
        return 0.0
    p_stems = [_stem(w) for w in p_words]
    score, used = 0.0, set()
    for tw in t_words:
        ts = _stem(tw)
        best, idx = -2.0, None
        for i, (pw, ps) in enumerate(zip(p_words, p_stems)):
            if ps == ts or pw == tw:
                s = 3.0
            elif len(ts) >= 3 and (pw.endswith(tw) or ps.endswith(ts)):   # Rispentomaten for Tomaten
                s = 2.0
            elif len(ts) >= 3 and pw.startswith(ts):       # Tomatenmark for Tomaten
                s = 0.5
            elif len(ts) >= 4 and ts in pw:
                s = 0.5
            else:
                continue
            if s > best:
                best, idx = s, i
        score += best
        if idx is not None:
            used.add(idx)
    term_text = " ".join(t_words)
    for i, pw in enumerate(p_words):
        proc = next((p for p in PROCESSED if pw == p or (pw.endswith(p) and len(pw) >= len(p) + 3)), None)
        if proc and proc not in term_text:                  # "Tomatenmark" when you asked for "Tomaten"
            score -= 1.5
        elif i not in used:
            score -= 0.15                                   # brand names, "Bio", sizes: barely matter
    return score / len(t_words)


def fit_score(amounts: dict[str, float], unit_quantity: str | None) -> float:
    """0..0.5: how little would be left over buying this pack for the amount needed."""
    size, unit = core.pack_size(unit_quantity)
    need = (amounts or {}).get(unit, 0) if size else 0
    if not size or need <= 0 or unit == "":
        return 0.0
    packs = max(1, math.ceil(need / size - 1e-9))
    return 0.5 * need / (packs * size)


def rank(term: str, hits: list, amounts: dict[str, float] | None = None) -> tuple[list, float | None]:
    """Picnic hits re-ordered best first, plus the best name score (None without hits)."""
    if not hits:
        return [], None
    scored = []
    for pos, h in enumerate(hits[:SEARCH_LIMIT]):
        ns = name_score(term, h.name)
        scored.append((ns + fit_score(amounts or {}, getattr(h, "unit_quantity", None)) - 0.1 * pos, ns, pos, h))
    scored.sort(key=lambda x: (-x[0], x[2]))
    return [x[3] for x in scored[:SHOW]], max(x[1] for x in scored)


def search(api, term: str, original: str) -> tuple[list, str]:
    """Search Picnic for term; if nothing turns up, try the last word of a multi-word term (the
    product noun in German, "geräuchertes Paprikapulver" -> "Paprikapulver"), then the original name.
    Returns (hits, the term that found them)."""
    tries = [term]
    words = term.split()
    if len(words) > 1:
        tries.append(words[-1])
    if original.lower() != term.lower():
        tries.append(original)
    for t in tries:
        hits = list(api.search(t).items[:SEARCH_LIMIT])
        if hits:
            return hits, t
    return [], term


def remembered(mappings: dict, term_choices: dict, name: str, term: str) -> tuple[str | None, str]:
    """A product you picked before: for this exact ingredient, for the same ingredient written slightly
    differently ("Red onions" / "red onion"), or for another ingredient with the same search term."""
    from .translate import normalise
    if name in mappings:
        return mappings[name], "ingredient"
    key = normalise(name)
    if key and key in mappings:
        return mappings[key], "ingredient"
    t = (term or "").strip().lower()
    if t and t in term_choices:
        return term_choices[t], "search term"
    return None, ""
