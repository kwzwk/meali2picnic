"""Ranking, search fallbacks, remembered choices and automatic translation. Offline."""
import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import autotranslate, matching  # noqa: E402
from app.dictionary import Dictionary  # noqa: E402

P = lambda i, name, uq=None: NS(id=i, name=name, unit_quantity=uq, display_price=100)  # noqa: E731


def names(hits):
    return [h.name for h in hits]


# ---------------------------------------------------------------- ranking

def test_head_of_compound_beats_modifier():
    hits = [P("1", "Picnic Tomatenmark"), P("2", "Rispentomaten"), P("3", "Bio Tomaten")]
    ranked, best = matching.rank("Tomaten", hits)
    assert names(ranked)[:2] == ["Bio Tomaten", "Rispentomaten"]
    assert names(ranked)[-1] == "Picnic Tomatenmark"
    assert best >= matching.WEAK


def test_processed_forms_lose_unless_asked_for():
    hits = [P("1", "Zwiebelbaguette"), P("2", "Röstzwiebeln"), P("3", "Gemüsezwiebeln")]
    assert names(matching.rank("Zwiebeln", hits)[0])[0] in ("Gemüsezwiebeln", "Röstzwiebeln")
    assert names(matching.rank("Zwiebeln", hits)[0])[-1] == "Zwiebelbaguette"
    hits = [P("1", "Tomaten passiert"), P("2", "Tomatenmark 3-fach konzentriert")]
    assert names(matching.rank("Tomatenmark", hits)[0])[0] == "Tomatenmark 3-fach konzentriert"


def test_multiword_terms_and_brands():
    hits = [P("1", "Paprika rot"), P("2", "Edeka Paprikapulver edelsüß"), P("3", "Ostmann Paprikapulver geräuchert")]
    assert names(matching.rank("geräuchertes Paprikapulver", hits)[0])[0] == "Ostmann Paprikapulver geräuchert"
    hits = [P("1", "Hähnchenbrust Teilstücke"), P("2", "Hähnchenschenkel")]
    assert names(matching.rank("Hähnchenschenkel", hits)[0])[0] == "Hähnchenschenkel"


def test_pack_size_breaks_ties():
    hits = [P("1", "Spaghetti", "400 g"), P("2", "Spaghetti", "500 g")]
    ranked, _ = matching.rank("Spaghetti", hits, {"g": 500.0})
    assert ranked[0].id == "2"                                   # one pack, nothing left over


def test_picnic_order_kept_when_equal_and_weak_matches_flagged():
    hits = [P("1", "Basmati Reis"), P("2", "Basmati Reis")]
    assert [h.id for h in matching.rank("Reis", hits)[0]] == ["1", "2"]
    _, best = matching.rank("Sumach", [P("1", "Za'atar Gewürzmischung")])
    assert best < matching.WEAK
    assert matching.rank("x", []) == ([], None)


# ---------------------------------------------------------------- search fallbacks

class Api:
    def __init__(self, catalog):
        self.catalog, self.searched = catalog, []

    def search(self, term):
        self.searched.append(term)
        return NS(items=self.catalog.get(term.lower(), []))


def test_search_falls_back_to_product_noun_then_original():
    api = Api({"paprikapulver": [P("1", "Paprikapulver")]})
    hits, term = matching.search(api, "geräuchertes Paprikapulver", "smoked paprika")
    assert term == "Paprikapulver" and hits[0].id == "1"
    api = Api({"gochujang": [P("g", "Gochujang")]})
    hits, term = matching.search(api, "koreanische Chilipaste", "gochujang")
    assert api.searched == ["koreanische Chilipaste", "Chilipaste", "gochujang"] and term == "gochujang"
    assert matching.search(Api({}), "Nichts", "nothing") == ([], "Nichts")


def test_remembered_by_name_spelling_and_term():
    maps, terms = {"red onions": "a", "zwiebel": "z"}, {"rote zwiebeln": "b", "zwiebeln": "c"}
    assert matching.remembered(maps, terms, "red onions", "rote Zwiebeln") == ("a", "ingredient")
    assert matching.remembered(maps, terms, "Zwiebel (gewürfelt)", "x") == ("z", "ingredient")
    assert matching.remembered({}, terms, "onions", "Zwiebeln") == ("c", "search term")
    assert matching.remembered({}, {}, "onions", "Zwiebeln") == (None, "")


# ---------------------------------------------------------------- automatic translation

class FakeBackend:
    name = "fake"

    def __init__(self, table=None, fail=False):
        self.table, self.fail, self.calls = table or {}, fail, []

    def translate(self, names):
        self.calls.append(list(names))
        if self.fail:
            raise RuntimeError("boom")
        return {n: self.table[n] for n in names if n in self.table}


def test_dictionary_first_then_one_auto_batch_then_cache(tmp_path):
    be = FakeBackend({"gochujang": "Gochujang", "smoked tofu": "Räuchertofu"})
    d = Dictionary(tmp_path, be)
    out = d.translate_all(["onions", "gochujang", "smoked tofu", "mystery"])
    assert out == {"onions": ("Zwiebel", "dictionary"), "gochujang": ("Gochujang", "auto"),
                   "smoked tofu": ("Räuchertofu", "auto"), "mystery": ("mystery", "none")}
    assert be.calls == [["gochujang", "smoked tofu", "mystery"]]       # one request for all unknowns
    d.translate_all(["Smoked Tofu", "gochujang"])
    assert len(be.calls) == 1                                          # cached, also for other spellings
    d.set("smoked tofu", "Tofu geräuchert")                            # your dictionary wins
    assert d.translate_all(["smoked tofu"])["smoked tofu"] == ("Tofu geräuchert", "dictionary")


def test_auto_failure_never_breaks_and_retries_later(tmp_path):
    be = FakeBackend(fail=True)
    d = Dictionary(tmp_path, be)
    assert d.translate_all(["sumac"]) == {"sumac": ("sumac", "none")}
    assert "boom" in d.auto.last_error
    be.fail, be.table = False, {"sumac": "Sumach"}
    assert d.translate_all(["sumac"]) == {"sumac": ("Sumach", "auto")}


def test_no_backend_means_names_pass_through(tmp_path):
    d = Dictionary(tmp_path)
    assert d.translate_all(["sumac", "onion"]) == {"sumac": ("sumac", "none"), "onion": ("Zwiebel", "dictionary")}


def test_suggestions_in_review_list_and_confirm_all(tmp_path):
    d = Dictionary(tmp_path, FakeBackend({"sumac": "Sumach", "tofu puffs": "Tofu-Puffs"}))
    d.translate_all(["sumac", "tofu puffs"])
    d.note_unknown("sumac", found=True)
    d.note_unknown("tofu puffs", found=False)
    rows = {r["name"]: r for r in d.untranslated()}
    assert rows["sumac"]["suggestion"] == "Sumach"
    assert d.keep_found() == 1
    assert d.translate("sumac") == ("Sumach", True)                   # the suggestion was confirmed
    assert [r["name"] for r in d.untranslated()] == ["tofu puffs"]


def test_bad_backend_output_is_not_cached(tmp_path):
    d = Dictionary(tmp_path, FakeBackend({"a": "x=y"}))
    assert d.translate_all(["a"]) == {"a": ("a", "none")}
    assert d.auto.cached() == {}


def test_make_backend_from_env(monkeypatch):
    for k in ("AUTO_TRANSLATE", "ANTHROPIC_API_KEY", "DEEPL_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert autotranslate.make_backend() is None
    monkeypatch.setenv("DEEPL_API_KEY", "k:fx")
    b = autotranslate.make_backend()
    assert b.name == "deepl" and "api-free" in b.url
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert autotranslate.make_backend().name == "claude"               # Claude preferred when both are set
    monkeypatch.setenv("AUTO_TRANSLATE", "deepl")
    assert autotranslate.make_backend().name == "deepl"
    monkeypatch.setenv("AUTO_TRANSLATE", "off")
    assert autotranslate.make_backend() is None


def test_deepl_sends_cleaned_names(monkeypatch):
    sent = {}

    def post(url, data, headers, timeout):
        sent.update(data=data, headers=headers)
        return NS(raise_for_status=lambda: None,
                  json=lambda: {"translations": [{"text": "Basilikum"}, {"text": "Hähnchenschenkel"}]})
    monkeypatch.setattr(autotranslate.requests, "post", post)
    out = autotranslate.DeepLBackend("k").translate(["large fresh basil leaves", "chicken thighs, boneless"])
    assert sent["data"]["text"] == ["basil", "chicken thighs"]
    assert sent["headers"]["Authorization"] == "DeepL-Auth-Key k"
    assert out == {"large fresh basil leaves": "Basilikum", "chicken thighs, boneless": "Hähnchenschenkel"}


def test_claude_backend_parses_and_ignores_unasked_names():
    calls = {}

    def parse(**kw):
        calls.update(kw)
        items = [NS(english="red onion", german="rote Zwiebeln"), NS(english="invented", german="X"),
                 NS(english="leek", german=" ")]
        return NS(stop_reason="end_turn", parsed_output=NS(items=items))
    be = autotranslate.ClaudeBackend.__new__(autotranslate.ClaudeBackend)
    be.model = "claude-opus-5-5"
    be.client = NS(messages=NS(parse=parse))
    assert be.translate(["red onion", "leek"]) == {"red onion": "rote Zwiebeln"}
    assert calls["model"] == "claude-opus-5-5" and "- red onion\n- leek" in calls["messages"][0]["content"]
    be.client = NS(messages=NS(parse=lambda **kw: NS(stop_reason="refusal", parsed_output=None)))
    assert be.translate(["leek"]) == {}
