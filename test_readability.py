import unittest

from readability import (
    FRE_TARGET,
    analyze,
    count_syllables,
    evaluate_gate,
    prose_from_blocks,
    prose_from_markdown,
    readability_report,
    strip_markup,
)

EASY = "Use short words. Keep it simple. This is easy to read."
HARD = (
    "Institutional considerations necessitate comprehensive organizational documentation "
    "notwithstanding jurisdictional heterogeneity."
)


class SyllableTests(unittest.TestCase):
    def test_common_words(self):
        for word, expected in [
            ("cat", 1),
            ("the", 1),
            ("make", 1),
            ("table", 2),
            ("simple", 2),
            ("easy", 2),
            ("editing", 3),
            ("readability", 5),
        ]:
            self.assertEqual(count_syllables(word), expected, word)

    def test_non_alphabetic_token_counts_one(self):
        self.assertEqual(count_syllables("2026"), 1)


class AnalyzeTests(unittest.TestCase):
    def test_counts_and_scores(self):
        result = analyze(EASY)
        self.assertEqual(result["sentences"], 3)
        self.assertEqual(result["words"], 11)
        self.assertGreater(result["flesch_reading_ease"], 90)

    def test_hard_text_scores_lower(self):
        self.assertLess(
            analyze(HARD)["flesch_reading_ease"],
            analyze(EASY)["flesch_reading_ease"],
        )

    def test_line_breaks_end_sentences(self):
        self.assertEqual(analyze("first item\nsecond item")["sentences"], 2)

    def test_empty_prose_raises(self):
        with self.assertRaises(ValueError):
            analyze("")


class ExtractionTests(unittest.TestCase):
    def test_strip_markup_removes_non_prose(self):
        text = (
            "# Heading\n"
            "| a | b |\n|---|---|\n| c | d |\n"
            "<div class=\"x\">Kept words</div>\n"
            "[su_box]inner shortcode prose stays[/su_box]\n"
            "A [link text](https://example.com/x) stays.\n"
            "Raw https://example.com/y goes.\n"
            "- bullet prose\n"
            "**bold** and *starred* words\n"
            "```\nfenced material vanishes\n```\n"
        )
        prose = strip_markup(text)
        self.assertNotIn("Heading", prose)
        self.assertNotIn("|", prose)
        self.assertNotIn("<div", prose)
        self.assertNotIn("su_box", prose)
        self.assertNotIn("example.com", prose)
        self.assertNotIn("fenced material", prose)
        self.assertNotIn("*", prose)
        self.assertIn("Kept words", prose)
        self.assertIn("inner shortcode prose stays", prose)
        self.assertIn("link text", prose)
        self.assertIn("bullet prose", prose)
        self.assertIn("bold and starred words", prose)

    def test_blocks_exclude_titles_faqs_and_product_titles(self):
        blocks = [
            {
                "title": "SectionTitleWord",
                "text": "Body prose stays.",
                "faqs": [{"question": "FaqQuestionWord?", "answer": "FaqAnswerWord stays out."}],
                "products": [{"title": "ProductTitleWord", "content": "Product prose stays."}],
            }
        ]
        prose = prose_from_blocks(blocks)
        self.assertIn("Body prose stays.", prose)
        self.assertIn("Product prose stays.", prose)
        self.assertNotIn("SectionTitleWord", prose)
        self.assertNotIn("FaqQuestionWord", prose)
        self.assertNotIn("FaqAnswerWord", prose)
        self.assertNotIn("ProductTitleWord", prose)

    def test_markdown_drops_faq_sections_by_heading(self):
        text = (
            "## Intro\nIntro prose.\n"
            "## Frequently Asked Questions\n### One?\nFaqAnswerWord here.\n"
            "## Verdict\nVerdict prose.\n"
        )
        prose = prose_from_markdown(text)
        self.assertIn("Intro prose.", prose)
        self.assertIn("Verdict prose.", prose)
        self.assertNotIn("FaqAnswerWord", prose)


class GateTests(unittest.TestCase):
    def test_passes_when_edits_clear_both_gates(self):
        report = readability_report([{"text": HARD}], [{"text": EASY}])
        self.assertTrue(report["gate"]["passed"])
        self.assertEqual(report["gate"]["reasons"], [])
        self.assertGreater(report["after"]["flesch_reading_ease"], FRE_TARGET)

    def test_fails_when_score_drops(self):
        report = readability_report([{"text": EASY}], [{"text": HARD}])
        self.assertFalse(report["gate"]["passed"])
        self.assertEqual(len(report["gate"]["reasons"]), 2)

    def test_fails_when_score_does_not_improve(self):
        gate = evaluate_gate(analyze(EASY), analyze(EASY))
        self.assertFalse(gate["passed"])
        self.assertTrue(any("does not improve" in reason for reason in gate["reasons"]))

    def test_fails_at_exactly_target(self):
        gate = evaluate_gate(
            {"flesch_reading_ease": 40.0},
            {"flesch_reading_ease": FRE_TARGET},
        )
        self.assertFalse(gate["passed"])


if __name__ == "__main__":
    unittest.main()
