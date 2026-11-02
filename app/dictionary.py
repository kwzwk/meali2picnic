"""The learning dictionary: your translations (a plain text file you can also edit by hand)
plus a list of ingredient names the app could not translate yet."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import translate
from .autotranslate import AutoTranslator

HEADER = "# english = deutsch  (one per line; added by the app or by you)\n"


class Dictionary:
    def __init__(self, data_dir: Path, backend=None):
        self.file = data_dir / "translations.txt"
        self.unknown_file = data_dir / "untranslated.json"
        self.lock = threading.Lock()
        self.auto = AutoTranslator(data_dir, backend)

    # ---- lookup
    def overrides(self) -> dict[str, str]:
        return translate.load_overrides(self.file)

    def translate(self, name: str) -> tuple[str, bool]:
        return translate.translate_ex(name, self.overrides())

    def translate_all(self, names: list[str]) -> dict[str, tuple[str, str]]:
        """name -> (search term, source). source is "dictionary", "auto" or "none".

        Your dictionary and the built-in list come first. Whatever they don't know goes to the
        automatic translator in one batch; anything still unknown is searched as written.
        """
        overrides = self.overrides()
        out, unknown = {}, []
        for n in names:
            term, known = translate.translate_ex(n, overrides)
            out[n] = (term, "dictionary")
            if not known:
                unknown.append(n)
        auto = self.auto.translate_many(unknown) if unknown else {}
        for n in unknown:
            out[n] = (auto[n], "auto") if n in auto else (n, "none")
        return out

    # ---- names we could not translate
    def _read_unknown(self) -> dict:
        try:
            return json.loads(self.unknown_file.read_text())
        except (OSError, ValueError):
            return {}

    def _write_unknown(self, data: dict):
        self.unknown_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.unknown_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        tmp.replace(self.unknown_file)

    def note_unknown(self, name: str, found: bool):
        """Remember a name that had no dictionary entry. Never raises: this is bookkeeping."""
        key = translate.normalise(name)
        if not key:
            return
        with self.lock:
            try:
                data = self._read_unknown()
                e = data.get(key, {"seen": 0})
                data[key] = {"seen": e["seen"] + 1, "found": found,
                             "last": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                self._write_unknown(data)
            except OSError:
                pass

    def untranslated(self) -> list[dict]:
        """Names without a match first, then the ones seen most often."""
        data = self._read_unknown()
        auto = self.auto.cached()
        rows = [{"name": k, "seen": v.get("seen", 1), "found": bool(v.get("found")), "suggestion": auto.get(k)}
                for k, v in data.items()]
        return sorted(rows, key=lambda r: (r["found"], -r["seen"], r["name"]))

    # ---- your translations
    def set(self, english: str, german: str):
        """Save english = german (empty german means: keep the name as it is). Raises ValueError on bad input."""
        key = translate.normalise(english)
        value = (german or "").strip() or key
        if not key:
            raise ValueError("Nothing to translate.")
        if any(c in value for c in ";=\n\r"):
            raise ValueError("The translation can't contain ; = or line breaks.")
        with self.lock:
            lines = self.file.read_text().splitlines() if self.file.exists() else [HEADER.strip()]
            new = f"{key} = {value}"
            for i, line in enumerate(lines):
                if "=" in line and not line.strip().startswith("#") and line.split("=", 1)[0].strip().lower() == key:
                    lines[i] = new
                    break
            else:
                lines.append(new)
            self.file.parent.mkdir(parents=True, exist_ok=True)
            self.file.write_text("\n".join(lines) + "\n")
            data = self._read_unknown()
            if data.pop(key, None) is not None:
                self._write_unknown(data)

    def keep_found(self) -> int:
        """Confirm every unknown name that already finds Picnic products: its automatic translation
        if it has one, otherwise the name as written. Returns how many."""
        rows = [r for r in self.untranslated() if r["found"]]
        for r in rows:
            self.set(r["name"], r["suggestion"] or r["name"])
        return len(rows)
