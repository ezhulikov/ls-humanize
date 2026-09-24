"""Parameterized publisher for confirmed humanization edits.

One tool for every post. Modes:

  check (default)   read-only: verify the live post matches the source draft
                    and that the target draft maps cleanly onto it
  --refresh-source  read-only remotely: regenerate the local source draft
                    from the live canonical blocks
  --publish         apply the approved edits (requires WP_ENABLE_WRITES=true)

Example:
  python publish_post.py --post-id 512909 --url https://venturesmarter.com/best-llc-services/delaware/
  python publish_post.py --post-id 512909 --url ... --publish
"""

import argparse
import copy
import difflib
import json
import shutil
import urllib.parse
from pathlib import Path

from purge_cache import purge_post_cache
from readability import readability_report
from wordpress_backup import create_backup
from workflow_state import post_workspace, publish_lock
from wp_client import basic_auth, ensure_writes_enabled, fetch_public, load_env, request_json

HERE = Path(__file__).resolve().parent
BAK = HERE / "bak"
DEFAULT_API_BASE = "https://venturesmarter.com/wp-json/wp/v2/posts"
TEXT_KEYS = {"text", "content", "content_2", "content_e", "pros", "cons", "answer", "question"}


def slug_from_url(url):
    segments = [segment for segment in urllib.parse.urlsplit(url).path.split("/") if segment]
    if not segments:
        raise RuntimeError(f"Cannot derive a slug from URL: {url}")
    return segments[-1]


def get_post(api_url, authorization, post_id, url, slug):
    post = request_json(f"{api_url}?context=edit", authorization)
    if post.get("id") != post_id or post.get("slug") != slug:
        raise RuntimeError(f"Unexpected target post: id={post.get('id')} slug={post.get('slug')}")
    if post.get("status") != "publish":
        raise RuntimeError(f"Target is not published: {post.get('status')}")
    if post.get("link") != url:
        raise RuntimeError(f"Canonical URL changed: {post.get('link')}")
    return post


def normalize(text):
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


def rendered_draft(blocks):
    parts = []
    for block in blocks:
        title = block.get("title")
        if isinstance(title, str) and title.strip():
            parts.append(f"## {title.strip()}")
        for key in ("text", "content", "content_2"):
            value = block.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value)
        faqs = block.get("faqs")
        if isinstance(faqs, list):
            for faq in faqs:
                if not isinstance(faq, dict):
                    continue
                question = str(faq.get("question") or "").strip()
                answer = faq.get("answer")
                if question:
                    parts.append(f"### {question}")
                if isinstance(answer, str) and answer.strip():
                    parts.append(answer)
        products = block.get("products")
        if isinstance(products, list):
            for product in products:
                if not isinstance(product, dict):
                    continue
                product_title = str(product.get("title") or product.get("brand") or "Provider")
                parts.append(f"### {product_title}")
                for key in ("content", "content_2", "content_e"):
                    value = product.get(key)
                    if isinstance(value, str) and value.strip():
                        parts.append(value)
    return "\n\n".join(parts)


def string_locations(value, path=()):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in TEXT_KEYS and isinstance(item, str):
                yield value, key, path + (key,)
            elif isinstance(item, (dict, list)):
                yield from string_locations(item, path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from string_locations(item, path + (index,))


def apply_local_edits(blocks, source, target):
    source_lines = source.splitlines(keepends=True)
    target_lines = target.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(None, source_lines, target_lines, autojunk=False)
    replacements = []
    for tag, start_a, end_a, start_b, end_b in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag != "replace":
            raise RuntimeError(f"Draft changes include a non-replacement hunk: {tag}")
        old = "".join(source_lines[start_a:end_a]).rstrip("\n")
        new = "".join(target_lines[start_b:end_b]).rstrip("\n")
        if not old:
            raise RuntimeError("Draft contains an unsupported insertion-only edit")
        replacements.append((old, new, start_a + 1))

    edited = copy.deepcopy(blocks)
    for old, new, line_number in replacements:
        matches = []
        for container, key, path in string_locations(edited):
            count = container[key].count(old)
            if count:
                matches.append((container, key, path, count))
        total = sum(item[3] for item in matches)
        if total != 1:
            raise RuntimeError(
                f"Approved edit at source line {line_number} matched {total} live fields: {old[:120]!r}"
            )
        container, key, path, _ = matches[0]
        container[key] = container[key].replace(old, new, 1)
    return edited, len(replacements)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def prepare_edits(post, source_path, target_path):
    live_blocks = json.loads(post["vs_blocks"])
    source = normalize(source_path.read_text(encoding="utf-8"))
    target = normalize(target_path.read_text(encoding="utf-8"))
    if normalize(rendered_draft(live_blocks)) != source:
        raise RuntimeError("Live canonical content differs from the local source; refusing to publish")
    edited_blocks, edit_count = apply_local_edits(live_blocks, source, target)
    if normalize(rendered_draft(edited_blocks)) != target:
        raise RuntimeError("The block payload does not reproduce the approved local draft")
    return edited_blocks, edit_count, readability_report(live_blocks, edited_blocks)


def main():
    parser = argparse.ArgumentParser(description="Check, refresh, or publish humanization edits for one post")
    parser.add_argument("--post-id", required=True, type=int)
    parser.add_argument("--url", required=True, help="canonical public URL of the live post")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--source", help="original draft path (default: work/post-<id>-<slug>/source.md)")
    parser.add_argument("--target", help="edited draft path (default: work/post-<id>-<slug>/edited.md)")
    parser.add_argument("--publish", action="store_true", help="apply the approved edits to the live post")
    parser.add_argument("--refresh-source", action="store_true", help="rebuild the source draft from the live post")
    args = parser.parse_args()
    if args.publish and args.refresh_source:
        parser.error("--publish and --refresh-source are mutually exclusive")

    post_id = args.post_id
    url = args.url if args.url.endswith("/") else args.url + "/"
    slug = slug_from_url(url)
    api_url = f"{args.api_base.rstrip('/')}/{post_id}"
    workspace = post_workspace(HERE, post_id, slug)
    source_path = Path(args.source) if args.source else workspace / "source.md"
    target_path = Path(args.target) if args.target else workspace / "edited.md"

    authorization = basic_auth(load_env())

    if args.refresh_source:
        post = get_post(api_url, authorization, post_id, url, slug)
        if source_path.exists():
            backup = source_path.with_name(f"{source_path.stem}-pre-refresh{source_path.suffix}")
            if not backup.exists():
                shutil.copy2(source_path, backup)
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(normalize(rendered_draft(json.loads(post["vs_blocks"]))), encoding="utf-8")
        print(json.dumps({"ok": True, "mode": "refresh-source", "post_id": post_id, "source": str(source_path), "modified": post.get("modified")}, indent=2))
        return

    if not args.publish:
        post = get_post(api_url, authorization, post_id, url, slug)
        edited_blocks, edit_count, readability = prepare_edits(post, source_path, target_path)
        print(json.dumps({"ok": readability["gate"]["passed"], "mode": "check", "post_id": post_id, "edit_count": edit_count, "modified": post.get("modified"), "readability": readability}, indent=2))
        return

    ensure_writes_enabled()
    # The lock spans only the final fresh fetch through verification, per prompt.md.
    with publish_lock(HERE, post_id, url):
        post = get_post(api_url, authorization, post_id, url, slug)
        edited_blocks, edit_count, readability = prepare_edits(post, source_path, target_path)
        if not readability["gate"]["passed"]:
            raise RuntimeError("Readability gate failed; refusing to publish: " + "; ".join(readability["gate"]["reasons"]))
        backup_dir = create_backup(BAK, post, api_url, ("vs_blocks",))
        response = request_json(
            api_url,
            authorization,
            method="POST",
            payload={"vs_blocks": json.dumps(edited_blocks, ensure_ascii=False, separators=(",", ":"))},
        )
        write_json(workspace / "publish-result.json", response)
        if json.loads(response.get("vs_blocks", "null")) != edited_blocks:
            raise RuntimeError("WordPress response does not match the approved payload")

        live_after = get_post(api_url, authorization, post_id, url, slug)
        if json.loads(live_after["vs_blocks"]) != edited_blocks:
            raise RuntimeError("WordPress readback does not match the approved payload")
        public_status, public_body = fetch_public(url)
        if public_status != 200:
            raise RuntimeError(f"Public URL returned HTTP {public_status}")
        write_json(workspace / "live-after.json", live_after)
        # The write is already verified; a purge failure degrades to a reported
        # error so the edge cache state is visible without failing the publish.
        try:
            purge = purge_post_cache(authorization, post_id, url)
        except Exception as error:  # noqa: BLE001
            purge = {"ok": False, "error": str(error)}
        write_json(workspace / "purge-result.json", purge)
    print(json.dumps({"ok": True, "mode": "publish", "post_id": post_id, "edit_count": edit_count, "status": live_after.get("status"), "link": live_after.get("link"), "modified": live_after.get("modified"), "public_status": public_status, "public_bytes": len(public_body), "backup": str(backup_dir), "readability": readability, "purge": purge}, indent=2))


if __name__ == "__main__":
    main()
