"""English -> German ingredient names, so English Mealie recipes find products in Picnic DE.

Built-in dictionary of common groceries + your own overrides in DATA_DIR/translations.txt
("english = deutsch", one per line). Unknown words, and anything already German, pass through unchanged.
"""
from __future__ import annotations

import re
from pathlib import Path

_BUILTIN = """
# vegetables
onion=Zwiebel; red onion=rote Zwiebel; spring onion=Frühlingszwiebel; scallion=Frühlingszwiebel; green onion=Frühlingszwiebel
shallot=Schalotte; garlic=Knoblauch; garlic clove=Knoblauch; ginger=Ingwer; potato=Kartoffel; sweet potato=Süßkartoffel
carrot=Möhre; celery=Staudensellerie; celeriac=Sellerie; leek=Lauch; tomato=Tomate; cherry tomato=Cherrytomaten
cucumber=Gurke; bell pepper=Paprika; red pepper=rote Paprika; green pepper=grüne Paprika; yellow pepper=gelbe Paprika
pepper=Pfeffer; chili=Chili; chilli=Chili; chili pepper=Chili; jalapeno=Jalapeño; zucchini=Zucchini; courgette=Zucchini
eggplant=Aubergine; aubergine=Aubergine; broccoli=Brokkoli; cauliflower=Blumenkohl; cabbage=Kohl; red cabbage=Rotkohl
white cabbage=Weißkohl; kale=Grünkohl; spinach=Spinat; lettuce=Salat; iceberg lettuce=Eisbergsalat; arugula=Rucola
rocket=Rucola; mushroom=Champignons; mushrooms=Champignons; corn=Mais; sweetcorn=Mais; peas=Erbsen; green beans=grüne Bohnen
beans=Bohnen; kidney beans=Kidneybohnen; black beans=schwarze Bohnen; chickpeas=Kichererbsen; lentils=Linsen
red lentils=rote Linsen; beetroot=Rote Bete; radish=Radieschen; pumpkin=Kürbis; butternut squash=Butternutkürbis
asparagus=Spargel; fennel=Fenchel; avocado=Avocado; olives=Oliven; pickles=Gewürzgurken; capers=Kapern
# fruit
apple=Apfel; banana=Banane; lemon=Zitrone; lime=Limette; orange=Orange; grapefruit=Grapefruit; pear=Birne
strawberry=Erdbeere; strawberries=Erdbeeren; blueberries=Heidelbeeren; raspberries=Himbeeren; grapes=Trauben
pineapple=Ananas; mango=Mango; peach=Pfirsich; cherry=Kirsche; plum=Pflaume; melon=Melone; watermelon=Wassermelone
raisins=Rosinen; dates=Datteln; lemon juice=Zitronensaft; lime juice=Limettensaft; orange juice=Orangensaft
# herbs & spices
parsley=Petersilie; cilantro=Koriander; coriander=Koriander; basil=Basilikum; thyme=Thymian; rosemary=Rosmarin
oregano=Oregano; mint=Minze; dill=Dill; chives=Schnittlauch; sage=Salbei; bay leaf=Lorbeerblatt; bay leaves=Lorbeerblätter
cinnamon=Zimt; cumin=Kreuzkümmel; paprika=Paprikapulver; smoked paprika=geräuchertes Paprikapulver; turmeric=Kurkuma
nutmeg=Muskatnuss; cloves=Nelken; cardamom=Kardamom; curry powder=Currypulver; chili flakes=Chiliflocken
chili powder=Chilipulver; cayenne pepper=Cayennepfeffer; black pepper=schwarzer Pfeffer; salt=Salz; sea salt=Meersalz
vanilla=Vanille; vanilla extract=Vanilleextrakt; vanilla sugar=Vanillezucker; saffron=Safran; mustard=Senf
# dairy & eggs
milk=Milch; whole milk=Vollmilch; butter=Butter; cream=Sahne; heavy cream=Sahne; whipping cream=Schlagsahne
sour cream=Saure Sahne; cream cheese=Frischkäse; yogurt=Joghurt; greek yogurt=Griechischer Joghurt; quark=Quark
cheese=Käse; cheddar=Cheddar; mozzarella=Mozzarella; parmesan=Parmesan; feta=Feta; feta cheese=Feta
goat cheese=Ziegenkäse; gouda=Gouda; ricotta=Ricotta; mascarpone=Mascarpone; egg=Eier; eggs=Eier; egg yolk=Eigelb
egg white=Eiweiß; buttermilk=Buttermilch; coconut milk=Kokosmilch; oat milk=Haferdrink; almond milk=Mandeldrink
# meat & fish
chicken=Hähnchen; chicken breast=Hähnchenbrust; chicken thigh=Hähnchenschenkel; chicken thighs=Hähnchenschenkel
ground beef=Hackfleisch; minced beef=Rinderhackfleisch; ground pork=Hackfleisch; minced meat=Hackfleisch
ground turkey=Putenhackfleisch; beef=Rindfleisch; steak=Steak; pork=Schweinefleisch; pork chop=Schweinekotelett
bacon=Speck; ham=Schinken; sausage=Wurst; sausages=Würstchen; turkey=Pute; lamb=Lammfleisch
salmon=Lachs; tuna=Thunfisch; cod=Kabeljau; shrimp=Garnelen; shrimps=Garnelen; prawns=Garnelen
# pantry
flour=Mehl; all-purpose flour=Weizenmehl; plain flour=Weizenmehl; wheat flour=Weizenmehl; whole wheat flour=Vollkornmehl
sugar=Zucker; brown sugar=brauner Zucker; powdered sugar=Puderzucker; icing sugar=Puderzucker; honey=Honig
maple syrup=Ahornsirup; baking powder=Backpulver; baking soda=Natron; yeast=Hefe; cornstarch=Speisestärke
cocoa=Kakao; cocoa powder=Kakaopulver; chocolate=Schokolade; dark chocolate=Zartbitterschokolade
rice=Reis; basmati rice=Basmatireis; pasta=Nudeln; spaghetti=Spaghetti; noodles=Nudeln; macaroni=Makkaroni
oats=Haferflocken; rolled oats=Haferflocken; bread=Brot; breadcrumbs=Paniermehl; tortilla=Tortilla; tortillas=Tortillas
couscous=Couscous; quinoa=Quinoa; bulgur=Bulgur; olive oil=Olivenöl; vegetable oil=Pflanzenöl; sunflower oil=Sonnenblumenöl
oil=Öl; coconut oil=Kokosöl; sesame oil=Sesamöl; vinegar=Essig; balsamic vinegar=Balsamico; white wine vinegar=Weißweinessig
apple cider vinegar=Apfelessig; soy sauce=Sojasauce; tomato paste=Tomatenmark; tomato sauce=Tomatensauce
canned tomatoes=Dosentomaten; chopped tomatoes=gehackte Tomaten; crushed tomatoes=passierte Tomaten
passata=Passata; ketchup=Ketchup; mayonnaise=Mayonnaise; peanut butter=Erdnussbutter; tahini=Tahini
vegetable stock=Gemüsebrühe; vegetable broth=Gemüsebrühe; chicken stock=Hühnerbrühe; chicken broth=Hühnerbrühe
beef stock=Rinderbrühe; stock=Brühe; broth=Brühe; stock cube=Brühwürfel; worcestershire sauce=Worcestershiresauce
hot sauce=scharfe Sauce; sriracha=Sriracha; pesto=Pesto; hummus=Hummus
# nuts & seeds
almonds=Mandeln; walnuts=Walnüsse; hazelnuts=Haselnüsse; cashews=Cashewkerne; peanuts=Erdnüsse
pine nuts=Pinienkerne; sesame seeds=Sesam; sunflower seeds=Sonnenblumenkerne; pumpkin seeds=Kürbiskerne
chia seeds=Chiasamen; flax seeds=Leinsamen; coconut=Kokosnuss; desiccated coconut=Kokosraspeln
# drinks
water=Wasser; white wine=Weißwein; red wine=Rotwein; wine=Wein; beer=Bier
"""

# Words that don't change which product you want.
_DESCRIPTORS = {
    "fresh", "large", "small", "medium", "big", "chopped", "diced", "sliced", "minced", "grated", "peeled",
    "whole", "ripe", "raw", "cooked", "boneless", "skinless", "frozen", "dried", "organic", "finely",
    "roughly", "optional", "extra", "virgin", "unsalted", "salted", "lean", "firm", "soft", "hot", "cold",
    "warm", "plain", "thick", "thin", "crushed", "cubed", "halved", "trimmed", "rinsed", "drained",
    "leaves", "leaf", "sprig", "sprigs", "fillet", "fillets", "florets", "stalk", "stalks", "bunch",
}


def _parse(text: str) -> dict[str, str]:
    table = {}
    for line in text.splitlines():
        if line.strip().startswith("#"):
            continue
        for pair in line.replace("\n", ";").split(";"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                if k.strip() and v.strip():
                    table[k.strip().lower()] = v.strip()
    return table


BUILTIN = _parse(_BUILTIN)


def load_overrides(path: Path) -> dict[str, str]:
    """Your own lines, 'english = deutsch'. They win over the built-in list."""
    try:
        return _parse(path.read_text())
    except OSError:
        return {}


def _variants(phrase: str):
    yield phrase
    if phrase.endswith("ies"):
        yield phrase[:-3] + "y"
    if phrase.endswith("es"):
        yield phrase[:-2]
    if phrase.endswith("s"):
        yield phrase[:-1]


def _normalise(name: str) -> str:
    n = re.sub(r"\(.*?\)", " ", name.lower())
    n = n.split(",")[0]                       # "onion, finely chopped" -> "onion"
    n = re.sub(r"[^a-zäöüß\- ]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    if " of " in n:
        n = n.split(" of ", 1)[1]
    return n


normalise = _normalise  # the key form used in the dictionary file


def translate(name: str, overrides: dict[str, str] | None = None) -> str:
    """Best German search term for an ingredient name. Returns the input unchanged if unsure."""
    return translate_ex(name, overrides)[0]


def translate_ex(name: str, overrides: dict[str, str] | None = None) -> tuple[str, bool]:
    """(search term, known). known is False when the dictionary had no entry, so the name passes through."""
    table = {**BUILTIN, **(overrides or {})}
    n = _normalise(name)
    if not n:
        return name, True

    def lookup(phrase: str):
        for v in _variants(phrase):
            if v in table:
                return table[v]
        return None

    if (hit := lookup(n)) is not None:
        return hit, True
    words = [w for w in n.split() if w not in _DESCRIPTORS]
    for i in range(len(words)):               # longest known tail: "large red onion" -> "red onion"
        if (hit := lookup(" ".join(words[i:]))) is not None:
            return hit, True
    return name, False
