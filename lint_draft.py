"""Mechanical Stage 1 gate for an edited draft against its source draft.

Turns the workflow's style rules into checks a runner can obey without
judgment. Read-only. Usage:

    python lint_draft.py work/post-<id>-<slug>/source.md work/post-<id>-<slug>/edited.md

ERRORS (exit 1, must be fixed before showing the edit table):
- structural drift: heading lines, list/link/table/emphasis markup counts,
  or the section-title sequence differ between source and edited draft
- any change inside a FAQ section
- a section's prose word count moved more than 5% from the source
- a changed line contains an em dash, a semicolon, a lowercase letter
  starting a new sentence, or a banned word or phrase

WARNINGS (allowed only with explicit judgment; see shared feedback rules —
"support" in its customer-service sense is fine, compound-sentence commas
before "and"/"or" are not Oxford commas):
- a changed line contains ", and"/", or" (possible Oxford comma), a possible
  trailing comma-negation contrast, a question mark, or a prose colon
- an unchanged non-FAQ line still contains a banned word or phrase
"""

import argparse
import difflib
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
BANNED_FILE = HERE / "shared" / "banned words.md"
FAQ_HEADING = re.compile(r"faq|frequently asked", re.IGNORECASE)
WORD = re.compile(r"[A-Za-z0-9']+")
WORD_SECTIONS = {"nouns", "verbs", "adjectives", "adverbs"}
PHRASE_SECTIONS = {"banned phrases", "brand-specific phrases"}
MAX_SECTION_DRIFT = 0.05
STRUCTURE_TOKENS = ("<h3", "<ul", "<ol", "<li", "<a ", "<strong", "<em", "<blockquote", "[table", "```")


def parse_banned(path=BANNED_FILE):
    words, phrases = set(), []
    section = None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        header = re.match(r"^\\\[(.+?)\\\]$", stripped)
        if header:
            section = header.group(1).lower()
            continue
        if not stripped or stripped.startswith("#"):
            continue
        if section in WORD_SECTIONS:
            words.update(item.strip().lower() for item in stripped.split(";") if item.strip())
        elif section in PHRASE_SECTIONS:
            phrases.extend(item.strip().lower() for item in stripped.split(";") if item.strip())
    pattern = re.compile(r"\b(?:" + "|".join(sorted(map(re.escape, words), key=len, reverse=True)) + r")\b")
    return pattern, sorted(set(phrases), key=len, reverse=True)


def clean_line(line):
    """Drop markup so punctuation and word checks see only prose."""
    line = re.sub(r"<[^>]+>", " ", line)
    line = re.sub(r"\[/?[A-Za-z][^\]]*\]", " ", line)
    line = re.sub(r"&[#A-Za-z0-9]+;", " ", line)
    line = re.sub(r"https?://\S+", " ", line)
    return line


def split_sections(lines):
    """Group draft lines into (title, [(line_number, text), ...]) by `## ` headings."""
    sections = [("(intro)", [])]
    for number, text in enumerate(lines, 1):
        stripped = text.strip()
        if stripped.startswith("## ") and not stripped.startswith("###"):
            sections.append((stripped[3:].strip(), []))
        else:
            sections[-1][1].append((number, text))
    return sections


def section_words(entries):
    return sum(len(WORD.findall(clean_line(text))) for _, text in entries if not text.strip().startswith("#"))


def changed_target_lines(source_lines, target_lines):
    matcher = difflib.SequenceMatcher(None, source_lines, target_lines, autojunk=False)
    changed = set()
    for tag, _start_a, _end_a, start_b, end_b in matcher.get_opcodes():
        if tag != "equal":
            changed.update(range(start_b + 1, end_b + 1))
    return changed


def structure_counts(text):
    counts = {"heading_lines": sum(1 for line in text.splitlines() if line.strip().startswith("#"))}
    for token in STRUCTURE_TOKENS:
        counts[token] = text.count(token)
    return counts


def lint(source_path, target_path):
    source = Path(source_path).read_text(encoding="utf-8")
    target = Path(target_path).read_text(encoding="utf-8")
    source_lines = source.splitlines()
    target_lines = target.splitlines()
    banned_pattern, banned_phrases = parse_banned()
    errors, warnings = [], []

    def error(line, rule, text):
        errors.append({"line": line, "rule": rule, "text": text.strip()[:160]})

    def warn(line, rule, text):
        warnings.append({"line": line, "rule": rule, "text": text.strip()[:160]})

    source_structure = structure_counts(source)
    target_structure = structure_counts(target)
    if source_structure != target_structure:
        drift = {key: (source_structure[key], target_structure[key])
                 for key in source_structure if source_structure[key] != target_structure[key]}
        error(0, "structure-drift", f"markup counts changed (source, edited): {drift}")

    source_sections = split_sections(source_lines)
    target_sections = split_sections(target_lines)
    if [title for title, _ in source_sections] != [title for title, _ in target_sections]:
        error(0, "section-titles-changed", "the `## ` heading sequence differs from the source")
    else:
        for (title, source_entries), (_, target_entries) in zip(source_sections, target_sections):
            if FAQ_HEADING.search(title):
                if [text for _, text in source_entries] != [text for _, text in target_entries]:
                    first = next((number for number, text in target_entries), 0)
                    error(first, "faq-modified", f"FAQ section '{title}' differs from the source")
                continue
            before, after = section_words(source_entries), section_words(target_entries)
            if before and abs(after - before) / before > MAX_SECTION_DRIFT:
                error(0, "section-word-count",
                      f"section '{title}' moved from {before} to {after} words ({(after - before) / before:+.1%})")

    faq_line_numbers = {
        number
        for title, entries in target_sections
        if FAQ_HEADING.search(title)
        for number, _ in entries
    }
    changed = changed_target_lines(source_lines, target_lines)

    def scan_banned(number, cleaned, raw, report):
        lowered = cleaned.lower()
        for match in banned_pattern.finditer(lowered):
            report(number, f"banned-word: {match.group(0)}", raw)
        for phrase in banned_phrases:
            if phrase in lowered:
                report(number, f"banned-phrase: {phrase}", raw)

    for number, raw in enumerate(target_lines, 1):
        stripped = raw.strip()
        if number in faq_line_numbers or not stripped or stripped.startswith("#"):
            continue
        cleaned = clean_line(raw)
        if number in changed:
            if "—" in cleaned:
                error(number, "em-dash", raw)
            if ";" in cleaned:
                error(number, "semicolon", raw)
            if re.search(r"[a-z]\.\s+[a-z]", cleaned):
                error(number, "lowercase-after-period", raw)
            scan_banned(number, cleaned, raw, error)
            if re.search(r",\s+(?:and|or)\s", cleaned):
                warn(number, "possible-oxford-comma", raw)
            if re.search(r",\s+not\s+(?:a|an|the|one)\b", cleaned):
                warn(number, "possible-trailing-negation", raw)
            if "?" in cleaned:
                warn(number, "question-in-prose", raw)
            if re.search(r":\s*\S", cleaned) and not stripped.endswith(":"):
                warn(number, "prose-colon", raw)
        else:
            scan_banned(number, cleaned, raw, warn)

    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "changed_lines": sorted(changed & set(range(1, len(target_lines) + 1)))}


def main():
    parser = argparse.ArgumentParser(description="Mechanically lint an edited draft against its source draft")
    parser.add_argument("source", help="source draft path (work/post-<id>-<slug>/source.md)")
    parser.add_argument("target", help="edited draft path (work/post-<id>-<slug>/edited.md)")
    args = parser.parse_args()
    result = lint(args.source, args.target)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
