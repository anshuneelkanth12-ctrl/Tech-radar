"""Self-tests. GitHub runs these before every update: if one fails, the update stops
and the published data stays exactly as it was."""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scraper import extract as ex  # noqa: E402
from scraper import run as pipeline  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures")


def price_for(title, media="", desc="", content=""):
    name = ex.extract_product_name(title)
    if not name:
        return None
    blocks = [("title", ex.clean_title(title)), ("media", ex.strip_html(media)),
              ("description", ex.strip_html(desc)), ("content", ex.strip_html(content))]
    found = ex.extract_price(blocks, name.core)
    return (found.currency, found.amount, found.is_from, found.previous) if found else None


class PriceRules(unittest.TestCase):
    def test_price_in_photo_caption(self):
        self.assertEqual(price_for("Oppo Enco X4 Review: Premium Sound That Finally Beats the Price Tag",
                                   media="Priced at Rs. 19,999, the Enco X4 is Oppo\u2019s premium TWS pair"),
                         ("INR", 19999.0, False, None))

    def test_bundled_extra_is_not_the_price(self):
        self.assertEqual(price_for("Noise Buds R1 Gaana Edition Launched in India With One-Year Gaana Plus Subscription",
                                   desc="The earbuds bundle a one-year Gaana Plus subscription worth Rs. 799 and sell for Rs. 1,399."),
                         ("INR", 1399.0, False, None))

    def test_no_price_means_no_price(self):
        self.assertIsNone(price_for("Apple HomePod Mini 2 Goes on Sale in India Today: Price, Specifications",
                                    desc="The smart speaker announced on October 13 is now available to buy in India."))

    def test_rival_price_in_same_article_is_ignored(self):
        self.assertEqual(price_for("Vivo X Fold 6 foldable phone launched in India with Dimensity 9500 Super Edition, 200MP camera: price, specifications",
                                   content="<ul><li>Vivo X Fold 6 is priced at Rs 2,19,999 for the only 12GB + 512GB model</li></ul>"
                                           "<p>It competes with the Samsung Galaxy Z Fold8 Ultra, which sells for less at Rs 1,94,999.</p>"),
                         ("INR", 219999.0, False, None))

    def test_starting_price(self):
        self.assertEqual(price_for("Vivo V80 launches in India with 7,200mAh battery, 50MP Zeiss cameras",
                                   content="<ul><li>Vivo V80 price in India starts at Rs 62,999.</li></ul><p>The 8GB + 256GB model costs Rs 69,999.</p>"),
                         ("INR", 62999.0, True, None))

    def test_price_threshold_is_ignored(self):
        self.assertEqual(price_for("Realme P4s Lite 4G smartphone launched in India with a 7,000mAh battery",
                                   content="<ul><li>Realme P4s Lite is priced under Rs 20,000.</li></ul>"
                                           "<p>The Realme P4s Lite price in India starts at Rs 16,499 for the 4GB + 64GB variant.</p>"),
                         ("INR", 16499.0, True, None))

    def test_currency_conversions_are_ignored(self):
        self.assertIsNone(price_for("Samsung Galaxy Buds On unveiled with open-ear design and Galaxy AI features",
                                    content="<p>They launch in South Korea for KRW 299,000, around Rs 19,000.</p>"))
        self.assertIsNone(price_for("Vivo S60t with Snapdragon 7 Gen 4 chipset debuts on Vivo's China website",
                                    content="<p>The 12GB + 256GB model is priced at CNY 3,599 (approx. Rs 51,000).</p>"))

    def test_named_model_wins_over_generic_sentence(self):
        self.assertEqual(price_for("GIGABYTE GS22F12I and GS27F16I FHD gaming monitors with up to 180Hz refresh rate launched in India",
                                   desc="GIGABYTE has launched two monitors. Prices start at Rs 5,750.",
                                   content="<p>The GS22F12I costs Rs 6,199, while the GS27F16I costs Rs 10,999.</p>"
                                           "<p>For around Rs 6,300, the Acer EK240Y is larger.</p>"),
                         ("INR", 6199.0, False, None))

    def test_deal_keeps_previous_price(self):
        self.assertEqual(price_for("Anker\u2019s 737 Power Bank drops to $79.99 (Reg. $110)"), ("USD", 79.99, False, 110.0))

    def test_lakh_notation(self):
        self.assertEqual(price_for("Samsung Galaxy Z Fold8 Ultra launched in India",
                                   desc="The Samsung Galaxy Z Fold8 Ultra is priced at Rs 1.95 lakh for the base model."),
                         ("INR", 195000.0, False, None))

    def test_variant_price_is_not_used_for_base_model(self):
        self.assertEqual(price_for("OPPO F35 series launched in India with 8,000mAh battery",
                                   content="<p>The OPPO F35 Pro starts at Rs 45,999.</p><p>The OPPO F35 is priced at Rs 38,999.</p>"),
                         ("INR", 38999.0, False, None))


class ProductNames(unittest.TestCase):
    CASES = [
        ("iPhone Air 2 Tipped to Get 120Hz OLED Display, Dual Cameras and 11 Percent Bigger Battery", "Apple iPhone Air 2", "Rumor"),
        ("Samsung's Galaxy Glasses to Launch in Select Markets in December: Report", "Samsung Galaxy Glasses", "Rumor"),
        ("Infinix GT 50 Pro Goes on Sale in India With 144Hz Display, Dimensity 8400 Ultimate Chip: Price, Offers", "Infinix GT 50 Pro", "On sale"),
        ("Vivo X Fold 6 foldable phone launched in India with Dimensity 9500 Super Edition, 200MP camera", "Vivo X Fold 6", "Launched"),
        ("OPPO Find X10 series confirmed to launch in India in the coming months", "OPPO Find X10", "Upcoming"),
        ("Realme P4s Lite 4G smartphone launched in India with a 7,000mAh battery", "Realme P4s Lite 4G", "Launched"),
        ("Samsung Galaxy Buds On unveiled with open-ear design and Galaxy AI features", "Samsung Galaxy Buds On", "Launched"),
        ("Apple to Reportedly Launch New iPad Air With OLED Screen, iPad Pro and Entry-Level iPad in H1 2027", "Apple iPad Air", "Rumor"),
        ("Lenovo Legion Go 2 launched in India with Ryzen Z2 Extreme", "Lenovo Legion Go 2", "Launched"),
    ]
    NOT_PRODUCTS = [
        "Best Samsung Smartphones Under Rs. 50,000 in India: Samsung Galaxy S25 FE, Galaxy A56 and More",
        "Infinix GT 50 Pro vs iQOO 15R vs OnePlus 15R: Price in India, Specifications and More Compared",
        "Nothing Announces 2027 Product Roadmap, Plans to Launch Over Twice as Many Smartphones Next Year",
        "Samsung Announces AI-powered Sleep Apnea Detection on Galaxy Watches and Ring: Here\u2019s How It Works",
        "Xiaomi festive sale offers revealed for smartphones, tablets, air purifiers and power banks",
        "iQOO smartphone offers for Amazon sale announced: iQOO Z11 Lite, iQOO 15R, iQOO 15 and more",
    ]

    def test_names_and_status(self):
        for title, name, lifecycle in self.CASES:
            with self.subTest(title=title):
                found = ex.extract_product_name(title)
                self.assertEqual(found.name if found else None, name)
                self.assertEqual(ex.detect_status(title)[0], lifecycle)

    def test_roundups_and_features_are_not_products(self):
        for title in self.NOT_PRODUCTS:
            with self.subTest(title=title):
                self.assertTrue(not ex.is_product_article(title) or ex.extract_product_name(title) is None)


class Categories(unittest.TestCase):
    def test_subject_decides(self):
        cases = [
            ("Infinix GT 50 Pro Goes on Sale in India With 144Hz Display, Dimensity 8400 Ultimate Chip", "Phones"),
            ("Binance Faces Historic Crypto Hack as Emergency Response and Market Fallout Unfold", "Security"),
            ("GIGABYTE GS22F12I and GS27F16I FHD gaming monitors with up to 180Hz refresh rate launched", "Computers"),
            ("Anker\u2019s 737 Power Bank drops to $79.99 (Reg. $110)", "Accessories"),
            ("Oppo Enco X4 Review: Premium Sound That Finally Beats the Price Tag", "Audio"),
        ]
        for title, category in cases:
            with self.subTest(title=title):
                self.assertEqual(ex.classify(title), category)


class Pipeline(unittest.TestCase):
    """A full update on saved feeds, then a second update on the same feeds."""

    @classmethod
    def setUpClass(cls):
        tmp = tempfile.mkdtemp()
        args = ["--fixtures", FIXTURES, "--now", "2026-11-02T08:00:00Z", "--news-days", "35"]
        first, second = os.path.join(tmp, "first.json"), os.path.join(tmp, "second.json")
        pipeline.main(args + ["--data", os.path.join(tmp, "none.json"), "--out", first])
        pipeline.main(args + ["--data", first, "--out", second])
        with open(first, encoding="utf-8") as fh:
            cls.first = json.load(fh)
        with open(second, encoding="utf-8") as fh:
            cls.second = json.load(fh)
        cls.products = {p["id"]: p for p in cls.first["products"]}

    def test_teaser_and_launch_become_one_product(self):
        p = self.products["vivo-x-fold-6"]
        self.assertEqual(p["status"], "Launched")
        self.assertEqual(len(p["articles"]), 2)
        self.assertEqual(p["prices"][-1]["amount"], 219999)

    def test_unlaunched_products_never_get_prices(self):
        for p in self.first["products"]:
            if p["status"] in ("Rumor", "Upcoming"):
                self.assertEqual(p["prices"], [], p["name"])

    def test_sponsored_posts_are_skipped(self):
        self.assertFalse(any("/sponsored/" in n["link"] for n in self.first["news"]))

    def test_quiet_feed_is_flagged(self):
        status = {s["name"]: s["status"] for s in self.first["sources"]}
        self.assertEqual(status["91mobiles"], "stale")
        self.assertEqual(status["Gadgets 360"], "ok")

    def test_big_stories_need_several_outlets(self):
        self.assertTrue(self.first["topics"])
        for topic in self.first["topics"]:
            self.assertGreaterEqual(len(topic["outlets"]), 2)
        self.assertTrue(any("Binance" in t["label"] and len(t["outlets"]) >= 3 for t in self.first["topics"]))

    def test_second_update_adds_nothing_twice(self):
        self.assertEqual(len(self.second["news"]), len(self.first["news"]))
        self.assertEqual(self.second["meta"]["counts"]["new_items"], 0)
        self.assertEqual({p["id"]: p["prices"] for p in self.second["products"]},
                         {p["id"]: p["prices"] for p in self.first["products"]})


if __name__ == "__main__":
    unittest.main()
