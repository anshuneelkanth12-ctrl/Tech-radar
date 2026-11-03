"""Classification, product detection and price extraction.

Design rule: never guess.
  * A product is only created when a headline names it in a recognisable way
    (a known brand or product line plus a model name or number).
  * A price is only recorded when one sentence ties that product to a currency
    amount with a pricing phrase ("priced at", "starts at", "drops to", ...).
  * Rumoured or upcoming products never get a price: "expected" prices are not facts.
Every recorded price keeps the article link and the exact sentence it came from,
so it can be checked by a human in one click.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------
_BLOCK_END_RE = re.compile(r"(?i)<\s*(?:br\s*/?|/p|/li|/h[1-6]|/tr|/div|/blockquote|/ul|/ol)\s*>")
_CELL_END_RE = re.compile(r"(?i)<\s*/t[dh]\s*>")
_TAG_RE = re.compile(r"<[^>]+>")
_SHORTCODE_RE = re.compile(r"\[/?[a-z_]+(?:\s[^\]]*)?\]", re.I)


def strip_html(raw: str | None) -> str:
    """HTML/RSS markup to plain text, keeping paragraph and table-row breaks as newlines."""
    if not raw:
        return ""
    s = re.sub(r"(?is)<(script|style)\b.*?</\1\s*>", " ", raw)
    s = _CELL_END_RE.sub(" | ", s)
    s = _BLOCK_END_RE.sub("\n", s)
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = _TAG_RE.sub(" ", s)  # double-escaped markup
    s = _SHORTCODE_RE.sub(" ", s)
    for ch in ("\u00a0", "\u2009", "\u202f"):
        s = s.replace(ch, " ")
    for ch in ("\u200b", "\u200e", "\u200f", "\ufeff"):
        s = s.replace(ch, "")
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r" *\n[\n ]*", "\n", s)
    return s.strip()


def clean_title(raw: str | None) -> str:
    return re.sub(r"\s+", " ", strip_html(raw)).strip()


def fold_quotes(s: str) -> str:
    return (s.replace("\u2019", "'").replace("\u2018", "'")
             .replace("\u201c", '"').replace("\u201d", '"'))


# Sentence spans that do not break on "Rs. 19,999", "approx. Rs 51,000", decimals, etc.
_ABBREV = {"rs", "approx", "vs", "inc", "co", "ltd", "mr", "mrs", "ms", "dr", "no", "st",
           "e.g", "i.e", "etc", "u.s", "u.k", "jr", "sr", "fig", "est", "corp", "reg"}
_SPLIT_RE = re.compile(r"[.!?](?=\s+[A-Z\u20b9$\u20ac\u00a3\"'\u201c\u2018(\[])|\n|\s\|\s(?=[A-Z])")


def sentence_spans(text: str) -> list[tuple[int, int]]:
    spans, start = [], 0
    for m in _SPLIT_RE.finditer(text):
        if m.group() == ".":
            before = re.search(r"([A-Za-z.]+)$", text[start:m.start()])
            if before and before.group(1).lower().rstrip(".") in _ABBREV:
                continue
        spans.append((start, m.end()))
        start = m.end()
    spans.append((start, len(text)))
    return [(a, b) for a, b in spans if text[a:b].strip()]


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------
# "strong" patterns name a device type (they decide the category and the
# left-most strong match gets a bonus); "weak" patterns are brand names that
# make several categories plausible.
CATEGORY_ORDER = [
    "Accessories", "Audio", "Wearables", "Tablets", "Computers", "Cameras & Drones",
    "TV & Home", "Gaming", "Phones", "Chips & Hardware", "AI & Software", "Security",
    "Business & Policy", "Science & EVs",
]
HARDWARE_CATEGORIES = {
    "Accessories", "Audio", "Wearables", "Tablets", "Computers", "Cameras & Drones",
    "TV & Home", "Gaming", "Phones", "Chips & Hardware",
}
DEFAULT_CATEGORY = "Tech News"

_RULES: dict[str, dict[str, list[str]]] = {
    "Accessories": {"strong": [
        r"power ?banks?", r"chargers?", r"charging (?:brick|pad|stand|dock|case|cable)s?", r"\bgan\b",
        r"magsafe", r"\bcables?\b", r"usb(?:-c)? (?:hubs?|docks?|cables?|adapters?)", r"docking stations?",
        r"\badapters?\b", r"(?:phone|iphone|galaxy|pixel|silicone|rugged|leather|clear|folio|wallet) cases?",
        r"\bcases? for\b", r"screen protectors?", r"tempered glass", r"wireless chargers?",
        r"(?:phone|laptop|charging|monitor|desk|tablet) stands?", r"(?:car|wall|desk|phone|monitor) mounts?",
        r"keyboards?", r"\bmouse\b", r"\bmice\b", r"webcams?", r"microphones?", r"\bssds?\b",
        r"external (?:drive|ssd|hdd)s?", r"hard drives?", r"memory cards?", r"micro ?sd", r"pen ?drives?",
        r"flash drives?", r"\bstylus(?:es)?\b", r"apple pencil", r"\bs pen\b", r"airtags?", r"tracker tags?",
        r"smart ?tags?", r"(?:game|gaming|wireless|xbox|ps5|pro) controllers?", r"dualsense", r"gamepads?",
        r"gimbals?", r"tripods?", r"ring lights?", r"watch (?:straps?|bands?)", r"accessor(?:y|ies)",
    ], "weak": [r"\banker\b", r"\bugreen\b", r"\bbelkin\b", r"\bspigen\b", r"\bbaseus\b", r"\bportronics\b",
                r"\bambrane\b", r"\blogitech\b", r"\bkeychron\b", r"\bsandisk\b", r"\bsamsung t\d"]},
    "Audio": {"strong": [
        r"earbuds", r"earphones", r"headphones", r"\btws\b", r"neckbands?", r"airpods", r"\bbuds\b",
        r"soundbars?", r"(?:bluetooth|smart|portable|party|home) speakers?", r"\bspeakers?\b", r"homepod",
        r"open-ear", r"\benco\b", r"noise[- ]cancell?(?:ing|ation)", r"\banc\b", r"\bdac\b", r"\bheadsets?\b",
    ], "weak": [r"\bjbl\b", r"\bbose\b", r"\bsennheiser\b", r"\bsonos\b", r"\bboat\b", r"\bboult\b",
                r"\bskullcandy\b", r"\bmarshall\b", r"\bsoundcore\b", r"\bnoise\b"]},
    "Wearables": {"strong": [
        r"smart ?watch(?:es)?", r"apple watch", r"galaxy watch", r"pixel watch",
        r"\bwatch (?:\d+|ultra|gt|fit|pro|se|series)\b", r"fitness (?:bands?|trackers?)", r"smart rings?",
        r"galaxy (?:ring|fit|watch)", r"\bglasses\b", r"\boura\b", r"\bwhoop\b", r"\bfitbit\b", r"\bgarmin (?:forerunner|fenix|venu|epix|instinct)",
        r"(?:smart|ai|ar|xr) glasses", r"ray-ban meta", r"vision pro", r"(?:vr|xr|ar|mixed[- ]reality) headsets?",
        r"\bquest \d", r"\bwearables?\b", r"\bsmart eyewear\b", r"\beyewear\b",
    ], "weak": [r"\bamazfit\b", r"\bgarmin\b", r"\bfire-boltt\b", r"\bultrahuman\b"]},
    "Tablets": {"strong": [
        r"\btablets?\b", r"\bipad\b", r"galaxy tab", r"\bpad (?:\d|air|pro|mini|plus)",
        r"(?:xiaomi|redmi|oneplus|oppo|honor|realme|vivo|iqoo|lenovo|nothing) pad", r"matepad",
        r"e-?readers?", r"\bkindle\b",
    ], "weak": []},
    "Computers": {"strong": [
        r"laptops?", r"macbook", r"chromebooks?", r"ultrabooks?", r"thinkpad", r"thinkbook",
        r"zenbook", r"vivobook", r"ideapad", r"\bxps\b", r"inspiron", r"\bspectre\b", r"\bomen\b",
        r"\bvictus\b", r"rog (?:zephyrus|strix|flow)", r"\bpredator\b", r"desktops?", r"mini pcs?",
        r"gaming pcs?", r"all-in-one", r"\bimac\b", r"mac mini", r"mac studio", r"mac pro", r"copilot\+ pcs?",
        r"\bmonitors?\b", r"\bpcs?\b", r"workstations?", r"\bmacs?\b",
    ], "weak": [r"\bdell\b", r"\blenovo\b", r"\basus\b", r"\bacer\b", r"\bhp\b", r"\bmsi\b", r"\bgigabyte\b"]},
    "Cameras & Drones": {"strong": [
        r"mirrorless", r"\bdslr\b", r"action cam(?:era)?s?", r"\bgopro\b", r"insta360",
        r"dji (?:osmo|pocket|mini|mavic|air|avata|neo|action|romo|flip)", r"\bdrones?\b", r"camera lens(?:es)?",
        r"vlogging cameras?", r"dash ?cams?", r"instant cameras?", r"(?:compact|point-and-shoot) cameras?",
    ], "weak": [r"\bcanon\b", r"\bnikon\b", r"\bfujifilm\b", r"\bleica\b", r"\bdji\b"]},
    "TV & Home": {"strong": [
        r"\btvs?\b(?!\+)", r"televisions?", r"projectors?", r"streaming (?:sticks?|devices?|box(?:es)?)",
        r"fire tv", r"google tv", r"chromecast", r"smart home", r"robot (?:vacuums?|cleaners?|mops?)",
        r"air purifiers?", r"water purifiers?", r"refrigerators?", r"\bfridges?\b", r"washing machines?",
        r"air conditioners?", r"smart (?:bulbs?|plugs?|locks?|lights?|displays?)", r"doorbells?",
        r"security cameras?", r"(?:wi-?fi|mesh) routers?", r"\brouters?\b", r"echo (?:show|dot|hub|pop|spot)",
        r"\balexa\b", r"google home", r"nest (?:hub|cam|thermostat)", r"homekit", r"vacuum cleaners?",
    ], "weak": [r"\bbravia\b", r"\btcl\b", r"\bhisense\b", r"\bdyson\b", r"\broborock\b", r"\beufy\b"]},
    "Gaming": {"strong": [
        r"playstation", r"\bps[56]\b", r"\bxbox\b", r"nintendo", r"switch 2", r"steam deck", r"steam machine",
        r"(?:gaming )?handhelds?", r"rog ally", r"legion go", r"\bconsoles?\b", r"\bgames?\b",
        r"esports", r"\bgta\b", r"\bsteam\b", r"game pass",
    ], "weak": [r"\bgaming\b"]},
    "Phones": {"strong": [
        r"smartphones?", r"\bphones?\b", r"\biphone\b", r"galaxy [samfz] ?\d+", r"galaxy z (?:fold|flip)",
        r"\bpixel \d+", r"\bnord\b", r"\bnarzo\b", r"oppo (?:reno|find|f\d+|a\d+|k\d+)", r"\breno ?\d+",
        r"\bfind x\d+", r"vivo (?:[xyvst]\d+|x fold)", r"\bmoto(?:rola)? (?:g|e|edge|razr)\b", r"\brazr\b",
        r"nothing phone", r"cmf phone", r"\bfoldables?\b", r"flip phones?", r"\bhandsets?\b",
        r"redmi (?:\d+|note|a\d+)", r"\bmobiles?\b", r"infinix (?:gt|note|hot|zero|smart)", r"tecno (?:spark|camon|pova|phantom)",
        r"realme (?:\d+|p\d+|c\d+|gt|narzo)", r"poco (?:[xfmc]\d+)", r"iqoo (?:\d+|z\d+|neo)", r"oneplus (?:\d+|nord|open)",
        r"motorola (?:edge|razr|signature|g\d+)", r"honor (?:magic|x\d+|\d+)", r"huawei (?:mate|pura|nova)",
        r"lava (?:agni|blaze|storm|bold|yuva|shark)", r"karbonn \w+", r"xiaomi \d+", r"google pixel \d+", r"galaxy f\d+",
    ], "weak": [r"\boneplus\b", r"\bredmi\b", r"\bpoco\b", r"\brealme\b", r"\biqoo\b", r"\binfinix\b",
                r"\btecno\b", r"\bitel\b", r"\bvivo\b", r"\boppo\b", r"\bmotorola\b", r"\bhonor\b",
                r"\bhuawei\b", r"\bkarbonn\b", r"\blava\b", r"\bxiaomi\b"]},
    "Chips & Hardware": {"strong": [
        r"\bchips?\b", r"chipsets?", r"processors?", r"\bcpus?\b", r"\bgpus?\b", r"\bsocs?\b", r"snapdragon",
        r"dimensity", r"exynos", r"tensor g\d", r"\bapple m\d+\b", r"\bm\d+(?: pro| max| ultra)? chips?\b",
        r"\ba\d{2} (?:pro|bionic)\b", r"intel core", r"core ultra", r"\bryzen\b", r"\bradeon\b", r"\brtx ?\d+",
        r"geforce", r"risc-?v", r"\btsmc\b", r"semiconductors?", r"foundr(?:y|ies)", r"\bdram\b", r"\bnand\b",
        r"\bddr\d\b", r"\bhbm\d?\b", r"memory (?:prices|shortage|chips)", r"motherboards?", r"graphics cards?",
    ], "weak": [r"\bnvidia\b", r"\bamd\b", r"\bintel\b", r"\bqualcomm\b", r"\bmediatek\b", r"\barm\b"]},
    "AI & Software": {"strong": [
        r"\bai\b", r"artificial intelligence", r"chatgpt", r"openai", r"\bgemini\b", r"\bclaude\b",
        r"anthropic", r"\bllms?\b", r"copilot", r"\bagents?\b", r"machine learning", r"deepmind", r"\bgrok\b",
        r"\bxai\b", r"mistral", r"\bllama\b", r"perplexity", r"deepseek", r"\bapps?\b", r"\bios \d+",
        r"ipados", r"android \d+", r"windows \d+", r"\bmacos\b", r"one ui", r"hyperos", r"coloros",
        r"originos", r"oxygenos", r"software", r"\bbrowsers?\b", r"whatsapp", r"instagram", r"youtube",
        r"spotify", r"netflix", r"telegram", r"social media", r"\bupdates?\b",
    ], "weak": [r"\bgoogle\b", r"\bmicrosoft\b", r"\bmeta\b", r"\bapple\b"]},
    "Security": {"strong": [
        r"\bhack(?:s|ed|ers?|ing)?\b", r"\bbreach(?:es)?\b", r"data leaks?", r"vulnerabilit(?:y|ies)",
        r"\bexploits?\b", r"malware", r"ransomware", r"spyware", r"cyber ?attacks?", r"cybersecurity",
        r"phishing", r"\bscams?\b", r"zero-day", r"\bcve-\d+", r"security flaws?", r"backdoors?", r"\bprivacy\b",
        r"\bheist\b", r"\btheft\b",
    ], "weak": []},
    "Business & Policy": {"strong": [
        r"lawsuits?", r"\bsues?\b", r"\bsued\b", r"\bcourt\b", r"antitrust", r"regulat(?:or|ors|ion|ions|ory)",
        r"\bbans?\b", r"\bbanned\b", r"tariffs?", r"export controls?", r"government", r"ministry", r"\btrai\b",
        r"\bmeity\b", r"\bfcc\b", r"european commission", r"\bdma\b", r"\bgdpr\b", r"\bfined?\b", r"layoffs?",
        r"\bipo\b", r"acquisitions?", r"\bacquires?\b", r"\bmerger\b", r"\bfunding\b", r"valuation",
        r"earnings", r"revenue", r"quarterly", r"\bq[1-4]\b", r"\bstocks?\b", r"market share", r"shipments",
        r"\bceo\b", r"resigns?", r"telecom", r"\bjio\b", r"airtel", r"vodafone idea", r"recharge", r"prepaid",
        r"postpaid", r"spectrum", r"\bupi\b", r"fintech", r"startups?", r"subscribers?",
    ], "weak": [r"crypto(?:currency|currencies)?", r"bitcoin", r"ethereum", r"binance", r"stablecoins?"]},
    "Science & EVs": {"strong": [
        r"\bspace\b", r"spacex", r"\bnasa\b", r"\bisro\b", r"rockets?", r"satellites?", r"starlink",
        r"\brobots?\b", r"humanoids?", r"robotics", r"quantum", r"\bfusion\b", r"\bevs?\b",
        r"electric (?:vehicles?|cars?|scooters?|bikes?)", r"\btesla\b", r"self-driving", r"autonomous",
        r"robotaxis?", r"waymo", r"\bclimate\b", r"\bsolar\b", r"\bnuclear\b", r"\bscience\b",
    ], "weak": []},
}
_COMPILED = {
    cat: {k: [re.compile(p, re.I) for p in pats] for k, pats in rules.items()}
    for cat, rules in _RULES.items()
}

# URL path segments that publishers use for sections.
_URL_HINTS = {
    "mobiles": "Phones", "mobile": "Phones", "phones": "Phones", "smartphones": "Phones",
    "laptops": "Computers", "computers": "Computers", "tablets": "Tablets", "audio": "Audio",
    "wearables": "Wearables", "home-appliances": "TV & Home", "smart-home": "TV & Home",
    "tv": "TV & Home", "cameras": "Cameras & Drones", "games": "Gaming", "gaming": "Gaming",
    "ai": "AI & Software", "apps": "AI & Software", "software": "AI & Software",
    "telecom": "Business & Policy", "cryptocurrency": "Business & Policy", "business": "Business & Policy",
    "policy": "Business & Policy", "security": "Security", "cyber-security": "Security",
    "science": "Science & EVs", "transportation": "Science & EVs", "cars": "Science & EVs", "space": "Science & EVs",
}


_SUBJECT_END_RE = re.compile(r"\s(?:with|featuring|powered by|sporting|packing|packs)\s|:\s|\s[\u2013\u2014-]\s|;\s", re.I)

# Brands that only make one kind of product: used when a headline names the
# brand and model but no device word ("Karbonn Rebel Pro confirmed to launch").
BRAND_DEFAULT_CATEGORY = {b: "Phones" for b in (
    "Karbonn", "Infinix", "Tecno", "Itel", "Lava", "iQOO", "Poco", "Realme", "Vivo", "Oppo", "OnePlus",
    "Motorola", "Nothing", "Xiaomi", "Redmi", "Honor", "Huawei", "Micromax", "Fairphone")}
BRAND_DEFAULT_CATEGORY.update({"Noise": "Audio", "boAt": "Audio", "Boult": "Audio", "JBL": "Audio",
                               "Bose": "Audio", "Sennheiser": "Audio", "Garmin": "Wearables",
                               "Amazfit": "Wearables", "Anker": "Accessories", "Ugreen": "Accessories",
                               "Spigen": "Accessories", "Belkin": "Accessories", "DJI": "Cameras & Drones",
                               "GoPro": "Cameras & Drones", "Insta360": "Cameras & Drones"})


def classify(title: str, summary: str = "", url: str = "") -> str:
    """Pick one category. The headline's subject (before "with ...", ": ...") decides;
    spec lists, the summary and the URL section only add supporting evidence."""
    t = fold_quotes(title)
    cut = _SUBJECT_END_RE.search(t)
    subject_end = cut.start() if cut else len(t)
    s = fold_quotes(summary[:600])
    score = {c: 0.0 for c in CATEGORY_ORDER}
    leftmost: tuple[int, int, str] | None = None
    for cat in CATEGORY_ORDER:
        rules = _COMPILED[cat]
        for rx in rules["strong"]:
            for m in rx.finditer(t):
                if m.start() < subject_end:
                    score[cat] += 3
                    key = (m.start(), CATEGORY_ORDER.index(cat), cat)
                    if leftmost is None or key < leftmost:
                        leftmost = key
                else:
                    score[cat] += 1
            score[cat] += min(2, len(rx.findall(s)))
        for rx in rules["weak"]:
            for m in rx.finditer(t):
                score[cat] += 1.5 if m.start() < subject_end else 0.5
            score[cat] += min(1, len(rx.findall(s))) * 0.5
    if leftmost:
        score[leftmost[2]] += 2
    for seg in re.findall(r"/([a-z0-9-]+)/", url.lower()):
        if seg in _URL_HINTS:
            score[_URL_HINTS[seg]] += 2
            break
    best = max(CATEGORY_ORDER, key=lambda c: (score[c], -CATEGORY_ORDER.index(c)))
    return best if score[best] >= 3 else DEFAULT_CATEGORY


_GAMING_HARDWARE_RE = re.compile(
    r"console|handheld|controller|ps[56]\b|xbox series|switch 2|steam deck|steam machine|rog ally|"
    r"legion go|headset|gpu|graphics card|monitor|gaming (?:phone|laptop|mouse|keyboard|chair)", re.I)


def is_hardware(category: str, title: str) -> bool:
    if category == "Gaming":
        return bool(_GAMING_HARDWARE_RE.search(title))
    return category in HARDWARE_CATEGORIES


# ---------------------------------------------------------------------------
# Article type (lifecycle status)
# ---------------------------------------------------------------------------
_REVIEW_RE = re.compile(r"\breview\b(?! (?:board|process|panel|committee))|\bhands[- ]on\b|"
                        r"\bfirst (?:look|impressions)\b|\bin for review\b|\blong[- ]term test\b", re.I)
_DEAL_RE = re.compile(r"price (?:cut|drop|slash(?:ed)?)|gets? (?:a )?price cut|gets cheaper|\bdiscount(?:ed)?\b|"
                      r"\bdeal\b|all-time low|lowest(?: ever)? price|\b(?:drops?|falls?|down|slashed) to (?:just |only )?"
                      r"(?:\$|\u20b9|rs\.?|inr)|sale price|\bon sale for\b|\bsave \$\d", re.I)
_UPCOMING_STRONG_RE = re.compile(r"launch date|launching (?:on|in|next|this|soon|today)|confirmed to launch|"
                                 r"set to launch|will launch|to launch on|\bteased?\b|\bteases\b|\bteaser\b|"
                                 r"pre-?orders?|pre-?book(?:ing|ings)?|coming soon|launch (?:set|confirmed|scheduled)|"
                                 r"ahead of (?:its )?(?:india |global |official )?launch|india launch (?:confirmed|date|timeline)",
                                 re.I)
_RUMOR_RE = re.compile(r"\btipped\b|\bleak(?:s|ed)?\b|\brumou?r(?:s|ed)?\b|\bspotted\b|\breportedly\b|"
                       r"\ballegedly\b|\bcertification\b|\bgeekbench\b|\brenders?\b|\bcancel(?:l)?ed\b|"
                       r"\bsurfaces?\b|\bpurported\b|\bexpected to\b|:\s*report\b|\b(?:may|could) (?:launch|come|get|feature|debut)\b|"
                       r"\bbenchmark|\bteardown\b", re.I)
_ON_SALE_RE = re.compile(r"goes on sale|go on sale|on sale (?:now|today|in)|sale (?:begins|starts|is live)|"
                         r"first sale|now available|available (?:now|for purchase|to buy|to order)|now on sale|"
                         r"starts selling|\bships? (?:now|today)\b|in stores now", re.I)
_LAUNCHED_RE = re.compile(r"\blaunched\b|\blaunches\b|\bunveil(?:s|ed)\b|\bannounce[sd]\b|\bintroduce[sd]\b|"
                          r"\bdebut(?:s|ed)\b|\breleased\b|\bgoes official\b|\bis official\b|\bofficially (?:launched|unveiled|announced)\b|"
                          r"\bmakes? (?:its )?debut\b|\barrives?\b", re.I)
_UPCOMING_WEAK_RE = re.compile(r"\bto launch\b|\bupcoming\b|\blaunch\b", re.I)

LIFECYCLE_RANK = {"Rumor": 0, "Upcoming": 1, "Launched": 2, "On sale": 3}


def detect_status(title: str) -> tuple[str | None, str | None]:
    """Return (lifecycle, event). lifecycle: Rumor/Upcoming/Launched/On sale. event: Review/Deal/None."""
    t = fold_quotes(title)
    if _REVIEW_RE.search(t):
        return "Launched", "Review"
    if _DEAL_RE.search(t):
        return "On sale", "Deal"
    if _UPCOMING_STRONG_RE.search(t):
        return "Upcoming", None
    if _RUMOR_RE.search(t):
        return "Rumor", None
    if _ON_SALE_RE.search(t):
        return "On sale", None
    if _LAUNCHED_RE.search(t):
        return "Launched", None
    if _UPCOMING_WEAK_RE.search(t):
        return "Upcoming", None
    return None, None


# ---------------------------------------------------------------------------
# Product names
# ---------------------------------------------------------------------------
# Brands matched case-insensitively.
_BRANDS_CI = [
    "Apple", "Samsung", "Google", "OnePlus", "Xiaomi", "Redmi", "Poco", "Realme", "Oppo", "Vivo", "iQOO",
    "Motorola", "Infinix", "Tecno", "Huawei", "Asus", "Acer", "Lenovo", "HP", "Dell", "MSI", "Razer",
    "Microsoft", "Sony", "LG", "Panasonic", "TCL", "Hisense", "Philips", "boAt", "Boult", "Fire-Boltt", "JBL",
    "Bose", "Sennheiser", "Skullcandy", "Marshall", "Anker", "Soundcore", "Ugreen", "Belkin", "Spigen",
    "Baseus", "Portronics", "Ambrane", "Amazon", "Meta", "DJI", "GoPro", "Insta360", "Canon", "Nikon",
    "Fujifilm", "Leica", "Nintendo", "Valve", "Nvidia", "AMD", "Intel", "Qualcomm", "MediaTek", "Garmin",
    "Amazfit", "Oura", "Ultrahuman", "Logitech", "Corsair", "HyperX", "SteelSeries", "Keychron", "Fairphone",
    "Gigabyte", "Zebronics", "Mivi", "Dyson", "Roborock", "Ecovacs", "Eufy", "Arlo", "TP-Link", "Netgear",
    "Alienware", "Karbonn", "Micromax", "CMF", "Sonos", "Shure", "Elgato", "Wacom", "Whoop",
    "Coros", "Suunto", "Withings", "Crossbeats", "Gizmore", "Itel", "SanDisk", "Seagate", "Kingston",
    "Lexar", "Cooler Master", "NZXT", "Teenage Engineering",
]
# Brand words that are also ordinary English words: only matched when capitalised.
_BRANDS_CS = ["Nothing", "Noise", "Honor", "Lava", "Pebble", "Beats", "Tile", "Native", "Framework", "Crucial", "Rode"]
# Product lines that imply a brand: (pattern, brand, case_sensitive)
_LINES = [
    (r"iPhone", "Apple"), (r"iPad", "Apple"), (r"MacBook", "Apple"), (r"iMac", "Apple"), (r"Mac mini", "Apple"),
    (r"Mac Studio", "Apple"), (r"Mac Pro", "Apple"), (r"AirPods", "Apple"), (r"AirTag", "Apple"),
    (r"HomePod", "Apple"), (r"Apple Watch", "Apple"), (r"Apple TV", "Apple"), (r"Vision Pro", "Apple"),
    (r"Galaxy", "Samsung"), (r"Exynos", "Samsung"), (r"Pixel", "Google"), (r"Chromecast", "Google"),
    (r"Fitbit", "Google"), (r"Xbox", "Microsoft"), (r"Surface", "Microsoft"), (r"PlayStation", "Sony"),
    (r"PS5", "Sony"), (r"PS6", "Sony"), (r"Xperia", "Sony"), (r"Bravia", "Sony"), (r"ThinkPad", "Lenovo"),
    (r"ThinkBook", "Lenovo"), (r"IdeaPad", "Lenovo"), (r"Legion", "Lenovo"), (r"Zenbook", "Asus"),
    (r"Vivobook", "Asus"), (r"ROG", "Asus"), (r"XPS", "Dell"), (r"Inspiron", "Dell"), (r"Spectre", "HP"),
    (r"Omen", "HP"), (r"Predator", "Acer"), (r"Kindle", "Amazon"), (r"Echo", "Amazon"), (r"Fire TV", "Amazon"),
    (r"Quest", "Meta"), (r"Ray-Ban Meta", "Meta"), (r"Nintendo Switch", "Nintendo"), (r"Steam Deck", "Valve"),
    (r"Narzo", "Realme"), (r"Nord", "OnePlus"), (r"Reno", "Oppo"), (r"Find X", "Oppo"), (r"Enco", "Oppo"),
    (r"Razr", "Motorola"), (r"Moto", "Motorola"), (r"GeForce", "Nvidia"), (r"RTX", "Nvidia"),
    (r"Radeon", "AMD"), (r"Ryzen", "AMD"), (r"Snapdragon", "Qualcomm"), (r"Dimensity", "MediaTek"),
]
_BRAND_CANON = {b.lower(): b for b in _BRANDS_CI + _BRANDS_CS}
_LINE_BRAND = {l.lower(): b for l, b in _LINES}

_brand_ci = "|".join(sorted((re.escape(b) for b in _BRANDS_CI), key=len, reverse=True))
_brand_cs = "|".join(re.escape(b) for b in _BRANDS_CS)
_lines = "|".join(sorted((re.escape(l) for l, _ in _LINES), key=len, reverse=True))
_ANCHOR_RE = re.compile(rf"(?<![\w-])(?:(?i:{_brand_ci})|{_brand_cs}|(?i:{_lines}))(?![\w])")
_POSSESSIVE_RE = re.compile(rf"(?<![\w-])((?i:{_brand_ci})|{_brand_cs})'s\b")

# Boundaries that end a product name. Function words only count when lowercase,
# because Title Case headlines keep them lowercase ("Goes on Sale in India")
# while real names may contain capitalised ones ("Galaxy Buds On").
_CI_BOUNDARY = (
    r"launch(?:ed|es|ing)?|goes|unveil(?:ed|s)?|announc(?:ed|es)|debut(?:s|ed)?|introduc(?:ed|es)|"
    r"review(?:ed)?|hands-on|gets?|receives?|tipped|leak(?:ed|s)?|spotted|teased?|teases|surfaces?|appears?|"
    r"listed|confirm(?:ed|s)?|prices?|priced|pricing|specifications?|specs|features?|design|renders?|"
    r"colou?rs?|colou?rways?|variants?|visits?|passes|certification|certified|arrives?|lands?|hits?|drops?|"
    r"falls?|offers?|deals?|discounts?|vs\.?|versus|could|might|reportedly|allegedly|expected|officially|"
    r"gains?|adds?|brings?|comes?|packs?|sports|powered|running|runs|update[ds]?|supports?|rolls?|starts?|"
    r"begins?|benchmark|tested|unboxing|alongside|ahead|with|without|will|sale|available|now|first|key|set|"
    r"should|which|where|after|before|amid|because|is|are|was|were|has|have|may|can|to|takes?|makes?|"
    r"uses?|reveals?|shows?|says?|said|tops?|beats?|outsells?|returns?|quietly|finally|also|just|today|"
    r"why|how|what|when|full|complete|official|global(?:ly)?|worldwide|launching|cancel(?:l)?ed|stolen"
)
_CS_BOUNDARY = r"and|in|on|at|for|of|by|as|from|under|via|the|a|an|than|that|but|or|its|their|into|over|amid"
_BOUNDARY_RE = re.compile(
    rf"\s*[:;|]\s|\s[\u2013\u2014]\s|\s-\s|,\s|\s\+\s|\s&\s|\s\d[\d,.]*\s?(?i:mah|hz|mp|gb|tb|w|-?inch|nits|mm)\b|"
    rf"\s(?i:{_CI_BOUNDARY})\b|\s(?:{_CS_BOUNDARY})\b"
)
_BRAND_VERB_RE = re.compile(
    rf"^(?P<brand>(?i:{_brand_ci})|{_brand_cs})\s+(?:(?i:is|has)\s+)?"
    r"(?:(?i:to|will|set to|reportedly|officially|quietly|finally|now)\s+)*"
    r"(?i:launch(?:es|ed)?|unveil(?:s|ed)?|announce[sd]?|introduce[sd]?|debut(?:s|ed)?|release[sd]?|"
    r"brings?|tease[sd]?|confirm(?:s|ed)?|reveal(?:s|ed)?|refresh(?:es|ed)?|drops?)\s+"
    r"(?:(?i:its|the|a|an|new|all-new|next-gen|first|latest|two|three|four|budget|flagship|premium)\s+)*"
    r"(?P<rest>.+)$"
)
_LEAD_NOISE_RE = re.compile(r"^(?:\[[^\]]+\]\s*|(?:exclusive|breaking|update|updated|watch|video|report|deal|"
                            r"review|hands-on)\s*[:|\u2013\u2014-]\s*)+", re.I)

_TRAILING_GENERIC = {
    "smartphone", "smartphones", "phone", "phones", "handset", "handsets", "device", "devices", "laptop",
    "laptops", "tablet", "tablets", "earbuds", "headphones", "smartwatch", "smartwatches", "monitor",
    "monitors", "foldable", "foldables", "flagship", "flagships", "model", "models", "series", "lineup",
    "range", "family", "variant", "variants", "band", "fitness", "tracker", "gaming", "budget", "mid-range",
    "midrange", "premium", "new", "fhd", "qhd", "4k", "oled", "wireless", "true", "smart", "india", "china",
    "global", "launch", "price", "officially", "duo", "trio", "speaker", "speakers",
}
_TRAILING_CAP_OK = {"Series", "Lineup", "Smartphone", "Smartphones", "Phones", "India", "China", "Global",
                    "Launch", "Price", "Handset", "Device", "Devices", "Range", "Family"}
PRODUCT_WORDS = {
    "pro", "max", "ultra", "plus", "mini", "lite", "air", "fe", "edge", "fold", "flip", "neo", "prime", "power",
    "edition", "buds", "watch", "band", "ring", "pad", "tab", "book", "phone", "tv", "speaker", "soundbar",
    "glasses", "headset", "charger", "bank", "hub", "dock", "case", "keyboard", "mouse", "monitor", "camera",
    "drone", "console", "controller", "stick", "cam", "vision", "studio", "display", "go", "se", "slim", "fit",
    "active", "classic", "sport", "open", "clip", "pods", "pencil", "pen", "stylus", "tag", "pocket", "deck",
    "on", "x", "s", "e", "r", "t", "gt", "turbo", "elite", "explorer", "discovery", "signature", "rebel",
    "glyph", "duo", "trio", "frame", "sense", "home", "show", "dot", "nano", "micro", "magic", "note", "zoom",
}
_GENERIC_REJECT_RE = re.compile(
    r"\b(?:users?|owners?|roadmap|plans?|program(?:me)?|partnership|offers?|sales?|services?|subscriptions?|"
    r"apps?|features?|deals?|discounts?|prices?|market|shipments?|revenue|report|event|festival|store|stores|"
    r"website|ecosystem|portfolio|update|updates|support|policy|team|ceo|stock|shares|brand|company|factory|"
    r"plant|detection|tracking|monitoring|mode|assistant|tool|tools|integration|account|accounts|platform|"
    r"approach|strategy|capabilities|ai-powered|teams|sources|engineer|unit|division|office|investment|"
    r"customers?|smartphone|smartphones|phones)\b", re.I)


@dataclass
class ProductName:
    name: str      # display name, e.g. "Apple iPhone Air 2"
    brand: str     # canonical brand, e.g. "Apple"
    key: str       # stable id, e.g. "apple-iphone-air-2"
    core: str      # name without the brand, e.g. "iPhone Air 2" (used to find price sentences)


def _anchor_brand(token: str) -> tuple[str, bool]:
    low = token.lower()
    if low in _BRAND_CANON:
        return _BRAND_CANON[low], False
    return _LINE_BRAND.get(low, ""), True


def _trim(candidate: str) -> str:
    words = candidate.split()
    while words:
        last = words[-1].strip(".,;:!?'\"")
        if last.lower() in _TRAILING_GENERIC and (last[:1].islower() or last in _TRAILING_CAP_OK
                                                   or last.isupper()):
            words.pop()
            continue
        break
    return " ".join(words).strip(" ,:;-\u2013\u2014'\"")


def product_key(name: str, brand: str) -> str:
    s = fold_quotes(name).lower()
    s = re.sub(r"\b(?:5g|4g|lte|smartphone|phone|series|new|the|wi-?fi)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    s = re.sub(r"^xiaomi (?=redmi|poco)", "", s)
    b = re.sub(r"[^a-z0-9]+", " ", brand.lower()).strip()
    if b and not s.startswith(b):
        s = f"{b} {s}"
    return re.sub(r"\s+", "-", s)


def _valid(words: list[str], brand: str, used_line: bool) -> bool:
    if len(words) < 2 or len(words) > 8:
        return False
    joined = " ".join(words)
    if _GENERIC_REJECT_RE.search(joined):
        return False
    brand_low = {brand.lower(), *(w.lower() for w in brand.split())}
    rest = [w for w in words if w.lower().strip("()") not in brand_low]
    if not rest:
        return False
    has_digit = any(re.search(r"\d", w) for w in rest)
    has_word = any(w.lower().strip("()") in PRODUCT_WORDS for w in rest)
    if not (has_digit or has_word or (used_line and len(rest) >= 2)):
        return False
    for w in rest:
        core = w.strip("()").lower()
        if w[:1].islower() and not re.search(r"\d", w) and core not in PRODUCT_WORDS \
                and core not in _LINE_BRAND:
            return False
    return True


def extract_product_name(title: str) -> ProductName | None:
    t = fold_quotes(clean_title(title))
    t = _LEAD_NOISE_RE.sub("", t)
    t = _POSSESSIVE_RE.sub(r"\1", t)
    m = _ANCHOR_RE.search(t)
    if not m or m.start() > 60:
        return None
    segment = t[m.start():]
    bv = _BRAND_VERB_RE.match(segment)
    if bv:
        brand_token, rest = bv.group("brand"), bv.group("rest")
        segment = f"{brand_token} {rest}"
        first_end = len(brand_token) + 1
    else:
        first_end = m.end() - m.start()
    cut = _BOUNDARY_RE.search(segment, first_end)
    candidate = segment[:cut.start()] if cut else segment
    candidate = re.sub(r"\b(?:New|All-New|new)\s+", "", candidate)
    candidate = _trim(candidate)
    if not candidate:
        return None
    words = candidate.split()
    brand, used_line = _anchor_brand(m.group(0))
    if not brand:
        return None
    if used_line:
        # The headline started with a product line ("iPhone Air 2"): show the brand too.
        if not words[0].lower() == brand.lower():
            words = brand.split() + words
    if not _valid(words, brand, used_line):
        return None
    name = " ".join(words)
    core_words = [w for w in words if w.lower() not in {brand.lower()}]
    return ProductName(name=name, brand=brand, key=product_key(name, brand), core=" ".join(core_words))


_EXCLUDE_PRODUCT_RE = re.compile(
    r"^best\b|\bbest\b.*\b(?:under|below|for|to buy)\b|\btop \d+\b|\bvs\.?\b|\bversus\b|\bcompared\b|"
    r"\bcomparison\b|\bhow to\b|\bguide\b|\bexplained\b|^what is\b|\bwhat to expect\b|\beverything we know\b|"
    r"\bshould you buy\b|^here(?:'s| is| are)\b|\blist of\b|\broundup\b|\bwishlist\b|\bgift guide\b|"
    r"\bdeals (?:on|for)\b|\bdeals\b.*\bsale\b|\bsale offers?\b|\boffers? (?:revealed|announced|listed|live)\b|"
    r"\boffers? for\b[^:]*\bsale\b|"
    r"\bfestive sale\b|\bbig billion\b|\bgreat indian festival\b|\b\d+ (?:best|things|reasons|ways|phones|laptops)\b|"
    r"\bweek in review\b|\bpodcast\b|\bnewsletter\b|\bopinion\b", re.I)
_EXCLUDE_URL_RE = re.compile(r"/(?:how-to|features|opinion|podcasts?|lists?|deals-roundup)/", re.I)


def is_product_article(title: str, url: str = "") -> bool:
    t = fold_quotes(title)
    return not _EXCLUDE_PRODUCT_RE.search(t) and not _EXCLUDE_URL_RE.search(url or "")


_SPONSORED_RE = re.compile(r"/(?:sponsored|brand-story|partner-content|advertorial)/|^sponsored\b|\bsponsored:", re.I)


def is_sponsored(title: str, url: str = "") -> bool:
    return bool(_SPONSORED_RE.search(url or "") or _SPONSORED_RE.search(title or ""))


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------
_PRICE_RE = re.compile(r"""
    (?P<cur>\u20b9|\bRs\.?|\bINR|\bUS\$|\$|\bUSD|\u20ac|\bEUR|\u00a3|\bGBP)
    \s?
    (?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)
    (?P<unit>\s?(?:lakhs?|lacs?|crores?|cr|billion|bn|million|mn|trillion|k)\b)?
""", re.X | re.I)
_CURRENCY = {"\u20b9": "INR", "rs": "INR", "rs.": "INR", "inr": "INR", "us$": "USD", "$": "USD", "usd": "USD",
             "\u20ac": "EUR", "eur": "EUR", "\u00a3": "GBP", "gbp": "GBP"}
_BOUNDS = {"INR": (99, 2_00_00_000), "USD": (1, 100_000), "EUR": (1, 100_000), "GBP": (1, 100_000)}

_REJECT_PRE_RE = re.compile(
    r"(?:roughly|approx\.?|approximately|about|around|nearly|~|equivalent to|converts? to|translates? to|"
    r"under|below|within|less than|up ?to|upto|budget of|worth|saving|save|saves|cashback of|discount of|"
    r"bonus of|exchange|emi|coupon|bank offers?|instant|extra|additional|flat|over|above|more than|beyond|"
    r"compared to|than|rival(?:s|ling)?|competes with|segment|bracket)\s*(?:of\s*)?(?:just\s*|only\s*)?$", re.I)
_REJECT_POST_RE = re.compile(
    r"^\s*\)?\s*(?:off\b|discount|cashback|bonus|per month|/month|a month|monthly|/mo\b|per year|/year|a year|"
    r"annually|/yr|per day|a day|subscription|plan\b|recharge|emi|instant|worth of|in (?:savings|discounts?|cashback)|"
    r"lower|less|cheaper|more|higher|budget|segment|range|bracket|price segment|mark\b|category|or (?:less|more|below|above))",
    re.I)
_PREVIOUS_PRE_RE = re.compile(r"(?:down from|was|were|reg\.?|regularly|originally|previously|usually|normally|"
                              r"instead of|list price of|mrp of|mrp)\s*:?\s*$", re.I)
_CUE_STRONG_RE = re.compile(
    r"(?:priced (?:at|from)|is priced|are priced|be priced|price (?:of|is|tag of|starts at|starts from|starting at)|"
    r"price in india (?:starts at|starts from|is|of|at)|prices? (?:start|starts|starting) (?:at|from)|"
    r"starts? (?:at|from)|starting (?:at|from)|costs?|retails? (?:at|for)|set at|sells? for|selling (?:at|for)|"
    r"available (?:at|for)|launched at|introduced at|comes? at|carries a price tag of|tagged at|"
    r"now (?:at|for|just|only)|drops? to|falls? to|down to|slashed to|for just|for only|at just|at only|"
    r"deal price|sale price|effective price|price:)\s*(?:of\s*)?(?:just\s*|only\s*)?$", re.I)
_CUE_WEAK_RE = re.compile(r"(?:\bat|\bfor|:|\||-|\u2013|\(|=)\s*$", re.I)
_FROM_RE = re.compile(r"(?:starts?|starting|prices? start|from)\s*(?:at|from)?\s*(?:just\s*|only\s*)?$", re.I)
_VARIANT_WORDS = r"Pro|Max|Ultra|Plus|\+|Lite|Mini|Neo|FE|Edge|Prime|Power|Air|SE|Turbo|GT|Fold|Flip|Elite|Explorer"

_BLOCK_WEIGHT = {"title": 6, "media": 4, "description": 3, "content": 1}


@dataclass
class Price:
    amount: float
    currency: str
    is_from: bool
    previous: float | None
    evidence: str
    score: int


def _core_patterns(core: str) -> tuple[re.Pattern | None, re.Pattern | None]:
    words = [w for w in re.split(r"\s+", fold_quotes(core).strip()) if w]
    words = [w.strip("()") for w in words if w.lower() not in {"5g", "4g", "lte"}]
    words = [w for w in words if w]
    if not words:
        return None, None
    sep = r"[\s()\-]*"
    full = re.compile(rf"(?<![\w]){sep.join(re.escape(w) for w in words)}(?![\w])(?!\s*(?:{_VARIANT_WORDS})\b)", re.I)
    partial = None
    if len(words) >= 3:
        partial = re.compile(rf"(?<![\w]){sep.join(re.escape(w) for w in words[:2])}(?![\w])"
                             rf"(?!\s*(?:{_VARIANT_WORDS})\b)", re.I)
    return full, partial


def _to_amount(num: str, unit: str | None) -> float | None:
    try:
        value = float(num.replace(",", ""))
    except ValueError:
        return None
    unit = (unit or "").strip().lower()
    if unit in {"lakh", "lakhs", "lac", "lacs"}:
        return value * 100_000
    if unit:  # crore / billion / million / k: not a gadget price
        return None
    return value


def extract_price(blocks: list[tuple[str, str]], core: str) -> Price | None:
    """Find the best-supported price for the product named by `core`.

    blocks: [("title", text), ("media", text), ("description", text), ("content", text)].
    """
    full_rx, part_rx = _core_patterns(core)
    best: Price | None = None
    previous: dict[str, float] = {}
    for kind, text in blocks:
        if not text:
            continue
        text = fold_quotes(text)
        spans = sentence_spans(text)
        for idx, (a, b) in enumerate(spans):
            sent = text[a:b]
            name_full = bool(full_rx and full_rx.search(sent))
            name_part = not name_full and bool(part_rx and part_rx.search(sent))
            first = idx == 0
            for m in _PRICE_RE.finditer(sent):
                currency = _CURRENCY.get(m.group("cur").lower())
                amount = _to_amount(m.group("num"), m.group("unit"))
                if not currency or amount is None:
                    continue
                lo, hi = _BOUNDS[currency]
                if not lo <= amount <= hi:
                    continue
                pre = sent[max(0, m.start() - 48):m.start()].rstrip(" (*")
                post = sent[m.end():m.end() + 32]
                if _REJECT_PRE_RE.search(pre) or _REJECT_POST_RE.search(post):
                    continue
                if _PREVIOUS_PRE_RE.search(pre):
                    previous.setdefault(currency, amount)
                    continue
                strong = bool(_CUE_STRONG_RE.search(pre))
                weak = not strong and bool(_CUE_WEAK_RE.search(pre))
                short = len(sent.split()) <= 14
                about_product = name_full or name_part or kind == "title" or (kind in ("media", "description") and first)
                if not about_product or not (strong or weak or short):
                    continue
                score = (_BLOCK_WEIGHT[kind] + (5 if name_full else 3 if name_part else 0)
                         + (4 if strong else 1 if weak else 0) + (1 if first else 0))
                if score < 7:
                    continue
                if best is None or score > best.score:
                    best = Price(amount=amount, currency=currency, is_from=bool(_FROM_RE.search(pre)),
                                 previous=None, evidence=re.sub(r"\s+", " ", sent).strip()[:240], score=score)
    if best and best.currency in previous and previous[best.currency] > best.amount:
        best.previous = previous[best.currency]
    return best
