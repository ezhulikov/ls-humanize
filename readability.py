"""Deterministic Flesch readability measurement for the humanize workflow.

Measurement procedure (authoritative):

1. Collect reader-facing prose. From block content this means each block's
   `text`, `content`, and `content_2` fields plus each product's `content`,
   `content_2`, and `content_e` fields. Block titles, product titles, and
   entire FAQ entries (questions and answers) are excluded because they are
   protected verbatim content the workflow is not allowed to edit.
2. Strip non-prose markup from the collected text: markdown headings, table
   lines, code fences and inline code, HTML tags and entities, shortcodes,
   image markup, bare URLs, list markers, and emphasis markers. Markdown
   links keep their anchor text and lose their URL.
3. Split prose into sentences at `.`, `!`, or `?` followed by whitespace or
   end of text. A line break also ends a sentence, so list items count as
   sentences.
4. Count words as maximal runs of letters, digits, and apostrophes.
5. Count syllables per word as the number of vowel groups (aeiouy), minus
   one for a silent trailing `e` that is not `le`, with a minimum of one.
   Tokens containing no letters count as one syllable.
6. Compute, rounded to one decimal:
   Flesch Reading Ease  = 206.835 - 1.015*(words/sentences) - 84.6*(syllables/words)
   Flesch-Kincaid Grade = 0.39*(words/sentences) + 11.8*(syllables/words) - 15.59
   Gates compare the rounded Flesch Reading Ease values.

Gates (enforced by publish_post.py before any remote write):

- The edited post must score strictly above 60.0 Flesch Reading Ease.
- The edited post must score strictly higher than the live post.

Command line:

    python readability.py <before> [<after>]

Each argument is either a markdown draft (`source.md`, `edited.md`) or a
JSON file holding the block payload (a raw `context=edit` API response with
a `vs_blocks` field, or a bare block list). With two arguments the report
includes the gate result. Markdown scoring drops FAQ sections only when
their `##` heading mentions FAQ, so block-based scoring, which is what
`publish_post.py` enforces, is authoritative.
"""

import argparse
import json
import re
from pathlib import Path

FRE_TARGET = 60.0

_FAQ_HEADING = re.compile(r"faq|frequently asked", re.IGNORECASE)
_WORD = re.compile(r"[A-Za-z0-9']+")
_VOWEL_GROUPS = re.compile(r"[aeiouy]+")


def count_syllables(word):
    """Count syllables with the documented vowel-group heuristic."""
    letters = re.sub(r"[^a-z]", "", word.lower())
    if not letters:
        return 1
    groups = len(_VOWEL_GROUPS.findall(letters))
    if groups > 1 and letters.endswith("e") and not letters.endswith("le"):
        groups -= 1
    return max(groups, 1)


def strip_markup(text):
    """Reduce markdown or block text to plain measurable prose."""
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`]*`", " ", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[/?[A-Za-z][^\]]*\]", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&[#A-Za-z0-9]+;", " ", text)
    text = re.sub(r"https?://\S+", " ", text)

    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("|") or not set(stripped) - set("|-: "):
            continue
        stripped = re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", stripped)
        stripped = stripped.replace("**", "").replace("__", "")
        stripped = re.sub(r"(?<!\w)[*_](?=\w)|(?<=\w)[*_](?!\w)", "", stripped)
        lines.append(stripped)
    return "\n".join(lines)


def prose_from_blocks(blocks):
    """Extract measurable prose from a block payload, excluding FAQ entries and titles."""
    parts = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        for key in ("text", "content", "content_2"):
            value = block.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value)
        products = block.get("products")
        if isinstance(products, list):
            for product in products:
                if not isinstance(product, dict):
                    continue
                for key in ("content", "content_2", "content_e"):
                    value = product.get(key)
                    if isinstance(value, str) and value.strip():
                        parts.append(value)
    return strip_markup("\n\n".join(parts))


def prose_from_markdown(text):
    """Extract measurable prose from a rendered draft, dropping FAQ `##` sections."""
    kept = []
    skipping = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## ") and not stripped.startswith("###"):
            skipping = bool(_FAQ_HEADING.search(stripped))
            continue
        if not skipping:
            kept.append(line)
    return strip_markup("\n".join(kept))


def analyze(text):
    """Return counts and both Flesch scores for extracted prose."""
    sentence_count = 0
    for line in text.splitlines():
        for chunk in re.split(r"[.!?]+(?=\s|$)", line):
            if _WORD.search(chunk):
                sentence_count += 1
    word_list = _WORD.findall(text)
    if not sentence_count or not word_list:
        raise ValueError("No measurable prose was found")

    word_count = len(word_list)
    syllable_count = sum(count_syllables(word) for word in word_list)
    words_per_sentence = word_count / sentence_count
    syllables_per_word = syllable_count / word_count
    return {
        "sentences": sentence_count,
        "words": word_count,
        "syllables": syllable_count,
        "flesch_reading_ease": round(206.835 - 1.015 * words_per_sentence - 84.6 * syllables_per_word, 1),
        "flesch_kincaid_grade": round(0.39 * words_per_sentence + 11.8 * syllables_per_word - 15.59, 1),
    }


def suggest_targets(text, top_sentences=8, top_words=15):
    """List the sentences and words dragging the score, for guided rewrites.

    Raising Flesch Reading Ease means splitting the longest sentences and
    swapping the heaviest words for several short ones. Deleting short filler
    words HURTS the score because it raises syllables per word.
    """
    sentences = []
    for line in text.splitlines():
        for chunk in re.split(r"[.!?]+(?=\s|$)", line):
            words_in_chunk = _WORD.findall(chunk)
            if len(words_in_chunk) > 20:
                sentences.append({"words": len(words_in_chunk), "text": chunk.strip()[:160]})
    heavy = {}
    for word in _WORD.findall(text):
        syllables = count_syllables(word)
        if syllables >= 3:
            entry = heavy.setdefault(word.lower(), {"word": word.lower(), "syllables": syllables, "count": 0})
            entry["count"] += 1
    return {
        "longest_sentences": sorted(sentences, key=lambda item: -item["words"])[:top_sentences],
        "heaviest_words": sorted(heavy.values(), key=lambda item: -item["syllables"] * item["count"])[:top_words],
    }


def evaluate_gate(before, after):
    """Apply both publish gates to a before/after pair of analyze() results."""
    reasons = []
    if after["flesch_reading_ease"] <= FRE_TARGET:
        reasons.append(
            f"edited Flesch Reading Ease {after['flesch_reading_ease']} is not above the required {FRE_TARGET}"
        )
    if after["flesch_reading_ease"] <= before["flesch_reading_ease"]:
        reasons.append(
            f"edited Flesch Reading Ease {after['flesch_reading_ease']} does not improve on the live score "
            f"{before['flesch_reading_ease']}"
        )
    return {"target": FRE_TARGET, "passed": not reasons, "reasons": reasons}


def readability_report(before_blocks, after_blocks):
    """Score two block payloads and evaluate the publish gates."""
    before = analyze(prose_from_blocks(before_blocks))
    after = analyze(prose_from_blocks(after_blocks))
    return {"before": before, "after": after, "gate": evaluate_gate(before, after)}


def _load_prose(path):
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(raw)
        if isinstance(data, dict):
            blocks_field = data.get("vs_blocks")
            data = json.loads(blocks_field) if isinstance(blocks_field, str) else blocks_field
        if not isinstance(data, list):
            raise ValueError(f"{path} does not contain a block list")
        return prose_from_blocks(data), "blocks"
    return prose_from_markdown(raw), "markdown"


def main():
    parser = argparse.ArgumentParser(description="Score drafts or block payloads with the documented Flesch procedure")
    parser.add_argument("before", help="markdown draft or block JSON for the live content")
    parser.add_argument("after", nargs="?", help="markdown draft or block JSON for the edited content")
    parser.add_argument("--suggest", action="store_true",
                        help="list the longest sentences and heaviest words of the last input, to guide rewrites")
    args = parser.parse_args()

    before_prose, before_kind = _load_prose(args.before)
    result = {"before": {"path": args.before, "source": before_kind, **analyze(before_prose)}}
    kinds = {before_kind}
    suggest_prose = before_prose
    if args.after:
        after_prose, after_kind = _load_prose(args.after)
        kinds.add(after_kind)
        result["after"] = {"path": args.after, "source": after_kind, **analyze(after_prose)}
        result["gate"] = evaluate_gate(result["before"], result["after"])
        suggest_prose = after_prose
    if args.suggest:
        result["suggestions"] = suggest_targets(suggest_prose)
    if "markdown" in kinds:
        result["note"] = (
            "markdown scoring drops FAQ sections only by heading match; "
            "block-based scoring via publish_post.py is authoritative"
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
