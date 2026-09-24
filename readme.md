# Content Humanizer

This workflow removes AI-signature patterns from live WordPress posts through a mandatory review-and-confirmation step.

`RUNBOOK.md` is the step-by-step command sequence for a run; `prompt.md` holds the editing rules. Runs are designed to need no improvisation: every validation is a script with an exit code.

## Accepted input

Submit one or more public URLs of live posts. The workflow rejects pasted content, local input files, post IDs, slugs, draft or preview links, editor links, API URLs, and unpublished posts.

Example request:

```text
Run the humanizer for https://example.com/path/to/live-post/
```

## Workflow

1. The workflow validates that every URL is a public page backed by a WordPress post whose status is `publish`.
2. It reads the canonical editable post content and checks for matching Surfer SEO guidance.
3. It proposes every change in a review table:

   | Edit # | Original Verbatim | Replacement |
   |---:|---|---|
   | 1 | Exact text from the live post | Exact proposed replacement |

4. Below each table it shows a before/after readability report. The edited post must score strictly above 60 Flesch Reading Ease and strictly above the live post's score.
5. It asks you to confirm those exact edits and stops. No remote content is changed during this stage.
6. After you explicitly confirm the current table, it fetches the live post again. If the content changed in the meantime, it cancels publication and asks you to approve a newly generated table.
7. If the post is unchanged, it re-checks the readability gates, then creates a checksummed, restorable backup in `bak/` before making any remote write.
8. It publishes only the approved replacements and verifies the live result.

Approval applies only to the table immediately above it. Revising an edit creates a new table and requires new confirmation. Text not shown in the table is never part of the approved change.

For multiple URLs, all URLs are validated first and each receives its own numbered table. Validation or stale-content failure on any URL prevents the entire batch from being published.

## Supporting files

```text
humanize/
|-- shared/
|   |-- banned words.md     prohibited words and phrases
|   `-- pangram.md          AI-pattern reference
|-- RUNBOOK.md              exact command sequence for a run, in order
|-- prompt.md               workflow instructions
|-- resolve_post.py         URL -> post resolution by exact canonical-link match
|-- lint_draft.py           mechanical style gate for edited drafts (exit 1 on errors)
|-- purge_cache.py          one-shot LiteSpeed + Cloudflare purge with public verification
|-- readability.py          documented Flesch scoring and the readability gates
|-- workflow_state.py       per-post paths and publishing locks
|-- wp_client.py            shared credentials, auth, and HTTP helpers
|-- wordpress_backup.py     checksummed backup creation and validation
|-- publish_post.py         the one parameterized publisher for every post
|-- restore_wordpress_backup.py  backup validation and restore
`-- README.md
```

Runtime directories (`bak/`, `work/`, `input/`, `output/`, and `surfer/`) are
created or populated locally and are intentionally excluded from version
control. Machine-local credentials and tool settings are excluded as well.

If a Surfer SEO export matches a post URL slug, protected keywords found under `IMPORTANT TERMS TO USE` are preserved verbatim. Each post stores these in `work/post-<id>-<slug>/protected-keywords.md`, so concurrent pages cannot overwrite one another. Without a match, only that post's keyword file is cleared.

## Readability requirement

Every run must leave the post easier to read, measured by Flesch Reading Ease on this standard scale:

| Flesch Reading Ease | Reading level |
|---|---|
| 90-100 | Very easy (5th grade) |
| 60-70 | Plain English (8th-9th grade), the target band for these posts |
| 30-50 | Difficult (college) |
| 0-30 | Very difficult (academic or technical) |

Flesch-Kincaid Grade is reported alongside it. Grade 7-8 suits the general public, grade 9-12 suits trade readers, and college-level prose is out of scope for these posts.

### Measurement procedure

Scores are computed only by `readability.py`; its module docstring is the authoritative procedure. In short:

1. Prose is collected from the post's editable block fields. Headings, block and product titles, FAQ questions and answers, tables, markup, shortcodes, and URLs are excluded, because the workflow either cannot edit them or a reader does not read them as sentences.
2. Sentences split at `.`, `!`, or `?`, and line breaks also end sentences. Words are runs of letters, digits, and apostrophes. Syllables use a fixed vowel-group heuristic.
3. Flesch Reading Ease = `206.835 - 1.015*(words/sentences) - 84.6*(syllables/word)`. Flesch-Kincaid Grade = `0.39*(words/sentences) + 11.8*(syllables/word) - 15.59`. Both are rounded to one decimal.

Score drafts or block payloads directly:

```text
python readability.py work/post-<id>-<slug>/source.md work/post-<id>-<slug>/edited.md
python readability.py work/post-<id>-<slug>/raw_post.json
```

### Gates

The proposal stage shows a before/after readability table for every post, and `publish_post.py` recomputes both scores from block content and refuses to write unless:

- the edited post scores strictly above 60.0 (hard line), and
- the edited post scores strictly above the live post.

Check mode reports the same `readability` block with `ok` reflecting the gate, so a failing plan is visible before confirmation. If FAQ protection, keyword protection, or the word count limit make the target unreachable, the run stops with a report instead of publishing.

## Publishing

All publishing goes through `publish_post.py`. It runs read-only by default, and every remote write anywhere in the toolchain requires `WP_ENABLE_WRITES=true` (enforced centrally in `wp_client.py`).

```text
python publish_post.py --post-id <id> --url <canonical-url>                    # check only
python publish_post.py --post-id <id> --url <canonical-url> --refresh-source  # rebuild source draft
$env:WP_ENABLE_WRITES="true"
python publish_post.py --post-id <id> --url <canonical-url> --publish
```

After its readback verification, `--publish` runs a full cache purge through `purge_cache.py`, ported from the vs workflows' guarded publish transport. It deploys a one-shot, token-guarded Code Snippets PHP snippet that purges LiteSpeed, fires the LiteSpeed purge actions, and uses the Cloudflare credentials stored in WordPress options to purge the post's URLs and the zone. The snippet removes itself, leftovers are deleted and re-checked, and the public page is then fetched with real-browser user agents to report `CF-Cache-Status` and `Age` per variant. Without this step Cloudflare's edge keeps serving the pre-write page long after a verified write. A purge failure is reported in the publish output (`purge.ok: false`) but does not fail the verified publish; rerun it standalone:

```text
$env:WP_ENABLE_WRITES="true"
python purge_cache.py --post-id <id> --url <canonical-url> --must-contain "new text"
```

The check and refresh modes never take the publishing lock. `--publish` holds the post's lock from its fresh fetch through backup, write, readback, and public-page verification, and refuses to write if the live content no longer matches the approved source draft. Source and edited drafts default to `work/post-<id>-<slug>/source.md` and `edited.md`; override with `--source`/`--target`.

## Parallel runs

Different posts may be analyzed and published from separate chats at the same time. Every mutable analysis artifact must remain under its post workspace. Shared reference material in `shared/` and source exports in `surfer/` must be treated as read-only.

Stage 2 must use `publish_lock()` from `workflow_state.py` from the final fresh fetch through backup, write, readback and public-page verification. Batch runs use `publish_locks()`, which acquires locks in ascending post-ID order. Locks are keyed by WordPress post ID, so a second chat cannot publish or restore the same post concurrently. A lock conflict stops the later run without making a remote write and releases any batch locks it already acquired.

The lock is released automatically when its Python context exits. A lock left behind by an interrupted process must be investigated before manual removal; the workflow never assumes an old-looking lock is stale.

## Backups and restoration

Every pre-publish backup uses this folder format:

```text
bak/YYYYMMDDTHHMMSSffffffZ-post-<post-id>-<slug>/
|-- manifest.json
|-- post.json
`-- restore-payload.json
```

The manifest records the post identity, canonical URL, API endpoint, restorable fields and SHA-256 checksums. `post.json` is the complete pre-write editable API response. `restore-payload.json` contains only the fields that the publisher can safely write back.

Validate a backup without changing WordPress:

```text
python restore_wordpress_backup.py <backup-folder-name> --confirm-post-id <post-id>
```

Restore it after enabling intentional writes:

```text
$env:WP_ENABLE_WRITES="true"
python restore_wordpress_backup.py <backup-folder-name> --confirm-post-id <post-id> --restore
```

The restore process validates the checksums and post identity, saves another backup of the current live state, restores the saved fields, and verifies the live readback.

## Editing guarantees

- Headings and structural elements are preserved.
- FAQ answers are preserved verbatim and never included in the editing table.
- Each section stays within 5% of its original word count.
- The first stage is read-only.
- Publishing requires explicit confirmation after the edit table is shown.
- Edits must raise the Flesch Reading Ease score, and the edited post must score above 60. `publish_post.py` blocks any publish that fails either check.
- A fresh-content check prevents approved edits from being applied to a newer post version.
- Every publish and restore write is preceded by a restorable backup.
