import tempfile
import unittest
from pathlib import Path

from lint_draft import lint

SOURCE = """Intro prose stays here for the run.

## First Section

<p>The quick march covers a plain field and a small town by the river bank.</p>
<p>Visit the <a href="https://example.com/">official page</a> for records.</p>

## FAQs

### Is It Free?

Yes, and this answer must never be edited by the workflow at all.
"""


def run_lint(source, target):
    with tempfile.TemporaryDirectory() as folder:
        source_path = Path(folder) / "source.md"
        target_path = Path(folder) / "edited.md"
        source_path.write_text(source, encoding="utf-8")
        target_path.write_text(target, encoding="utf-8")
        return lint(source_path, target_path)


def rules(findings):
    return [item["rule"] for item in findings]


class LintDraftTests(unittest.TestCase):
    def test_clean_pass(self):
        target = SOURCE.replace("The quick march covers", "The fast walk crosses")
        result = run_lint(SOURCE, target)
        self.assertTrue(result["ok"])
        self.assertEqual(result["errors"], [])

    def test_semicolon_and_em_dash_are_errors(self):
        target = SOURCE.replace(
            "a plain field and a small town",
            "a plain field; a small town — twice",
        )
        result = run_lint(SOURCE, target)
        self.assertIn("em-dash", rules(result["errors"]))
        self.assertIn("semicolon", rules(result["errors"]))

    def test_banned_word_in_changed_line_is_error(self):
        target = SOURCE.replace("a plain field", "a robust field")
        result = run_lint(SOURCE, target)
        self.assertIn("banned-word: robust", rules(result["errors"]))

    def test_banned_word_in_unchanged_line_is_warning(self):
        source = SOURCE.replace("for records.", "for robust records.")
        target = source.replace("The quick march covers", "The fast walk crosses")
        result = run_lint(source, target)
        self.assertTrue(result["ok"])
        self.assertIn("banned-word: robust", rules(result["warnings"]))

    def test_oxford_comma_and_question_are_warnings(self):
        target = SOURCE.replace(
            "a plain field and a small town",
            "a field, a town, and a road. Ready to search?",
        )
        result = run_lint(SOURCE, target)
        self.assertIn("possible-oxford-comma", rules(result["warnings"]))
        self.assertIn("question-in-prose", rules(result["warnings"]))

    def test_faq_edit_is_error(self):
        target = SOURCE.replace("this answer must never be edited", "this answer was edited")
        result = run_lint(SOURCE, target)
        self.assertIn("faq-modified", rules(result["errors"]))

    def test_structure_drift_is_error(self):
        target = SOURCE.replace('<a href="https://example.com/">official page</a>', "official page")
        result = run_lint(SOURCE, target)
        self.assertIn("structure-drift", rules(result["errors"]))

    def test_section_word_count_drift_is_error(self):
        target = SOURCE.replace(
            "The quick march covers a plain field and a small town by the river bank.",
            "The quick march covers a field.",
        )
        result = run_lint(SOURCE, target)
        self.assertIn("section-word-count", rules(result["errors"]))

    def test_heading_change_is_error(self):
        target = SOURCE.replace("## First Section", "## Renamed Section")
        result = run_lint(SOURCE, target)
        self.assertIn("section-titles-changed", rules(result["errors"]))

    def test_lowercase_after_period_is_error(self):
        target = SOURCE.replace("and a small town by the river bank.", "and a small town. by the river bank.")
        result = run_lint(SOURCE, target)
        self.assertIn("lowercase-after-period", rules(result["errors"]))


if __name__ == "__main__":
    unittest.main()
