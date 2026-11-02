"""Amazon pantry cart: ingredient -> remembered ASIN, and one "add to cart" link for all of them.

Amazon has no public API that searches products or fills a cart for a normal customer. What it
does still support is the Add-to-Cart form: a link with ASIN.n / Quantity.n pairs that puts those
products into the cart of whoever opens it, after they confirm on Amazon. So each ingredient is
matched to an ASIN once (by hand, from a search link we provide) and remembered.
"""
from __future__ import annotations

import math
import os
import re
from urllib.parse import quote_plus, urlencode

ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")
URL_ASIN_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d|o/ASIN|exec/obidos/ASIN)/([A-Z0-9]{10})(?:[/?#]|$)", re.I)


def domain() -> str:
    """Amazon store host, e.g. www.amazon.de. AMAZON_DOMAIN=off disables the feature."""
    d = os.environ.get("AMAZON_DOMAIN", "amazon.de").strip().lower()
    d = re.sub(r"^https?://", "", d).strip("/")
    if d in ("", "off", "false", "0", "no"):
        return ""
    return d if d.startswith("www.") else "www." + d


def enabled() -> bool:
    return bool(domain())


def extract_asin(text: str) -> str | None:
    """Accepts a bare ASIN or any Amazon product link; returns the ASIN or None."""
    text = (text or "").strip()
    if ASIN_RE.match(text.upper()):
        return text.upper()
    m = URL_ASIN_RE.search(text)
    return m.group(1).upper() if m else None


def search_url(term: str) -> str:
    return f"https://{domain()}/s?k={quote_plus(term)}"


def product_url(asin: str) -> str:
    return f"https://{domain()}/dp/{asin}"


def cart_url(lines: list[tuple[str, int]]) -> str:
    """Add-to-Cart form link. Opening it shows Amazon's confirmation page; nothing is ordered."""
    params: list[tuple[str, str]] = []
    n = 0
    for asin, count in lines:
        if count <= 0:
            continue
        n += 1
        params += [(f"ASIN.{n}", asin), (f"Quantity.{n}", str(count))]
    tag = os.environ.get("AMAZON_ASSOCIATE_TAG", "").strip()
    if tag:
        params.append(("AssociateTag", tag))
    return f"https://{domain()}/gp/aws/cart/add.html?" + urlencode(params)


def default_count(amounts: dict[str, float]) -> int:
    """Pantry packs: piece counts are taken as is, anything weighed or measured is one pack."""
    weighed = any(v > 0 for u, v in amounts.items() if u)
    pieces = amounts.get("", 0)
    if pieces > 0 and not weighed:
        return max(1, math.ceil(pieces - 1e-9))
    return 1
