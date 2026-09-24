# Humanize Runbook

Follow these steps in order, one post at a time. Never skip a step, never
reorder, never write your own publish or purge script. `prompt.md` holds the
editing rules; this file holds the exact commands. Every command runs from the
`humanize/` folder. Steps marked **STOP** end your turn until the user replies.

## Stage 1 — analyze and propose (read-only)

1. **Resolve the URL.** Accept only a public live-post URL.

   ```text
   python resolve_post.py <url>
   ```

   Requires `ok: true` and exactly one candidate with `match: true`. Use its
   `post_id`. Never pick a post by slug alone: the same slug exists in many
   silos, and the wrong-silo mistake has happened more than eight times.

2. **Read the references.** Read `shared/banned words.md` in full and
   `shared/pangram.md` before proposing any edit.

3. **Check for a Surfer file.** Look in `surfer/` for a filename sharing
   keywords with the slug. If found, extract every keyword under
   `IMPORTANT TERMS TO USE` and write them with `write_protected_keywords`
   from `workflow_state.py`. If not found, write an empty list. Either way the
   file lives at `work/post-<id>-<slug>/protected-keywords.md`.

4. **Build the source draft.**

   ```text
   python publish_post.py --post-id <id> --url <url> --refresh-source
   ```

5. **Get the readability baseline and targets.**

   ```text
   python readability.py work/post-<id>-<slug>/source.md --suggest
   ```

   The edited post must end strictly above 60.0 Flesch Reading Ease and
   strictly above this baseline. The suggestions list the sentences to split
   and the words to swap.

6. **Write `work/post-<id>-<slug>/edited.md`** by copying `source.md` and
   editing it under the rules in `prompt.md`. Strategy that works:
   - Split every sentence longer than 20 words.
   - Swap heavy words for several short ones (Verifying→Checking,
     available→free, counterparty's→the other side's). Keep proper nouns.
   - Never delete short filler words to "tighten" text: that raises
     syllables per word and LOWERS the score.
   - Keep every edit inside its original line. Never touch headings, FAQ
     sections, links, or markup.

7. **Lint mechanically.** Repeat until zero errors:

   ```text
   python lint_draft.py work/post-<id>-<slug>/source.md work/post-<id>-<slug>/edited.md
   ```

   Fix every `error`. For each `warning`, apply the documented judgment
   (compound-sentence ", and" is not an Oxford comma; "support" in its
   customer-service sense is allowed) and either fix it or be ready to say why
   it stays. Re-scan after every fix: replacement text has reintroduced banned
   words before.

8. **Run the authoritative gate check.** Repeat steps 6-8 until `ok: true`:

   ```text
   python publish_post.py --post-id <id> --url <url>
   ```

   This verifies the drafts map onto the live post and computes the
   block-based readability gate. If the score falls short, run
   `python readability.py work/post-<id>-<slug>/source.md work/post-<id>-<slug>/edited.md --suggest`
   and rewrite the listed targets. If FAQ, keyword, or word-count protection
   makes 60.0 unreachable, stop and report the conflict instead.

9. **STOP — show the proposal.** Present, per post: the canonical URL, the
   three-column edit table exactly as `prompt.md` specifies, and the
   before/after readability table. Ask the user to confirm publishing these
   exact edits. Do not publish in this turn.

## Stage 2 — publish (only after explicit confirmation of the current table)

10. **Publish.**

    ```text
    $env:WP_ENABLE_WRITES="true"
    python publish_post.py --post-id <id> --url <url> --publish
    ```

    This single command takes the lock, re-fetches, re-checks the gate,
    backs up, writes, verifies the readback, and runs the cache purge. If it
    reports a lock conflict, stop and report the owner metadata; never delete
    a lock. If it reports the live content changed, rebuild the table and go
    back to step 9.

11. **Verify the purge result.** In the output, `purge.ok` must be true and
    every variant must show `missing: []`. HTTP 200 alone proves nothing: the
    Cloudflare edge has served pre-write pages for 10+ minutes. If `purge.ok`
    is false, rerun the purge standalone and report the outcome either way:

    ```text
    python purge_cache.py --post-id <id> --url <url> --must-contain "<a new phrase>"
    ```

12. **Report.** State the canonical URL, the published edit numbers, the
    before/after scores, the backup folder name, and the purge outcome.

## Known traps

- `edit_count` may be lower than the table's row count when edits touch
  adjacent lines. That is diff hunk merging, not a bug.
- A "Six vs Seven categories" style scoring-count error recurs on RA pages:
  re-check every numeric claim your edits touch.
- `fetch_public()` returns `(status, body_bytes)`, not a string.
- The API `modified` timestamp changing between Stage 1 and Stage 2 means the
  fresh-content check will refuse to publish. That is correct behavior; rebuild
  the table.
- Never run anything from `legacy/`. Never write files outside
  `work/post-<id>-<slug>/` except through the tools above.
