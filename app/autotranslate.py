"""Automatic English -> German translation for ingredient names the dictionary doesn't know.

Two backends, picked from the environment (AUTO_TRANSLATE=auto, the default, uses the first key it finds):

- claude: ANTHROPIC_API_KEY. Translates into the term you would type into a German supermarket's
  search ("large fresh basil leaves" -> "Basilikum"), so it handles descriptors, brands and odd
  wording well. All unknown names of one plan go in a single request.
- deepl:  DEEPL_API_KEY (the free key works). A literal translation of the cleaned-up name.

Results are cached in DATA_DIR/auto_translations.json, so each name is translated once.
Your own dictionary (translations.txt) always wins over an automatic translation.
Nothing here ever raises into the app: if a backend fails, the name is searched as written.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import requests
from pydantic import BaseModel

from . import translate

log = logging.getLogger(__name__)

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
MAX_BATCH = 60

PROMPT = """You help an app put recipe ingredients into a cart at Picnic, a German online supermarket.
For each ingredient name below, give the German search term a shopper would type into Picnic's search
to find the right product.

- Use the everyday German product name, the way supermarkets label it (e.g. "red onion" -> "rote Zwiebeln",
  "chicken thighs, boneless" -> "Hähnchenschenkel", "large fresh basil leaves" -> "Basilikum",
  "smoked paprika" -> "geräuchertes Paprikapulver", "heavy cream" -> "Schlagsahne", "scallions" -> "Frühlingszwiebeln").
- Drop amounts, preparation words (chopped, minced, finely, ...) and size words, but keep words that change
  which product you buy (red, smoked, whole wheat, low-fat, canned, ...).
- Keep it short: one to three words, no brands, no explanations.
- If the name is already German, or is a product name that Germans use as is (e.g. "Gochujang"), return it unchanged.
- Return exactly one entry per input name, with "english" copied exactly from the input."""


class _Item(BaseModel):
    english: str
    german: str


class _Result(BaseModel):
    items: list[_Item]


def _clean(name: str) -> str:
    """Name without amounts, brackets and preparation words, for the literal backend."""
    words = [w for w in translate.normalise(name).split() if w not in translate.DESCRIPTORS]
    return " ".join(words) or name


class ClaudeBackend:
    name = "claude"

    def __init__(self, api_key: str, model: str = CLAUDE_MODEL):
        import anthropic  # only needed when this backend is used
        self.client = anthropic.Anthropic(api_key=api_key, timeout=60.0, max_retries=2)
        self.model = model

    def translate(self, names: list[str]) -> dict[str, str]:
        listing = "\n".join(f"- {n}" for n in names)
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=8000,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": f"{PROMPT}\n\nIngredient names:\n{listing}"}],
            output_format=_Result,
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            log.warning("Claude returned no translations (stop_reason=%s)", response.stop_reason)
            return {}
        wanted = set(names)
        return {i.english: i.german.strip() for i in response.parsed_output.items
                if i.english in wanted and i.german.strip()}


class DeepLBackend:
    name = "deepl"

    def __init__(self, api_key: str):
        self.key = api_key
        host = "api-free.deepl.com" if api_key.endswith(":fx") else "api.deepl.com"
        self.url = f"https://{host}/v2/translate"

    def translate(self, names: list[str]) -> dict[str, str]:
        cleaned = [_clean(n) for n in names]
        r = requests.post(self.url, timeout=30, headers={"Authorization": f"DeepL-Auth-Key {self.key}"},
                          data={"text": cleaned, "source_lang": "EN", "target_lang": "DE"})
        r.raise_for_status()
        out = [t["text"].strip() for t in r.json().get("translations", [])]
        return {n: t for n, t in zip(names, out) if t}


def make_backend():
    """The configured backend, or None when automatic translation is off or has no key."""
    choice = os.environ.get("AUTO_TRANSLATE", "auto").strip().lower()
    claude_key, deepl_key = os.environ.get("ANTHROPIC_API_KEY"), os.environ.get("DEEPL_API_KEY")
    try:
        if choice in ("claude", "auto") and claude_key:
            return ClaudeBackend(claude_key)
        if choice in ("deepl", "auto") and deepl_key:
            return DeepLBackend(deepl_key)
    except Exception as e:  # e.g. the anthropic package is missing
        log.warning("Automatic translation disabled: %s", e)
    return None


class AutoTranslator:
    def __init__(self, data_dir: Path, backend=None):
        self.file = data_dir / "auto_translations.json"
        self.backend = backend
        self.lock = threading.Lock()
        self.last_error: str | None = None

    @property
    def enabled(self) -> bool:
        return self.backend is not None

    def _read(self) -> dict:
        try:
            return json.loads(self.file.read_text())
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict):
        try:
            self.file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True))
            tmp.replace(self.file)
        except OSError as e:
            log.warning("Could not save automatic translations: %s", e)

    def cached(self) -> dict[str, str]:
        """normalised english -> german, for everything translated so far."""
        return {k: v["de"] for k, v in self._read().items() if isinstance(v, dict) and v.get("de")}

    def translate_many(self, names: list[str]) -> dict[str, str]:
        """Translate names (keyed by the name as given). Uses the cache; one backend call for the rest."""
        cache = self.cached()
        out, todo = {}, []
        for n in names:
            key = translate.normalise(n)
            if key in cache:
                out[n] = cache[key]
            elif key and n not in todo:
                todo.append(n)
        if not todo or not self.backend:
            return out
        fresh: dict[str, str] = {}
        for i in range(0, len(todo), MAX_BATCH):
            try:
                fresh.update(self.backend.translate(todo[i:i + MAX_BATCH]))
                self.last_error = None
            except Exception as e:
                self.last_error = f"{self.backend.name}: {e}"
                log.warning("Automatic translation failed: %s", self.last_error)
                break
        if fresh:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            with self.lock:
                data = self._read()
                for n, de in fresh.items():
                    if any(c in de for c in ";=\n\r"):
                        continue
                    data[translate.normalise(n)] = {"de": de, "by": self.backend.name, "at": now}
                    out[n] = de
                self._write(data)
        return out
