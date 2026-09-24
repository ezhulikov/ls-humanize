"""Resolve a live public URL to its WordPress post.

Mechanizes the wrong-silo check: the same slug (e.g. `alabama`) exists in many
silos, so a post must be selected by exact canonical `link` match, never by
slug alone. Read-only.

Usage:
    python resolve_post.py <public-url>

Prints every published candidate sharing the slug and a JSON result for the
exact match. Exits 1 when the page is not live or no candidate's link matches.
"""

import argparse
import json
import urllib.parse

from wp_client import basic_auth, fetch_public, load_env, request_json

API = "https://venturesmarter.com/wp-json/wp/v2/posts"


def resolve(url):
    url = url if url.endswith("/") else url + "/"
    status, body = fetch_public(url)
    if status != 200:
        return {"ok": False, "url": url, "error": f"public page returned HTTP {status}"}
    segments = [segment for segment in urllib.parse.urlsplit(url).path.split("/") if segment]
    if not segments:
        return {"ok": False, "url": url, "error": "cannot derive a slug from the URL"}
    slug = segments[-1]
    authorization = basic_auth(load_env())
    query = urllib.parse.urlencode({"slug": slug, "status": "publish", "context": "edit", "per_page": 100})
    candidates = request_json(f"{API}?{query}", authorization)
    rows = [
        {"post_id": post.get("id"), "status": post.get("status"), "link": post.get("link"),
         "modified": post.get("modified"), "match": post.get("link") == url}
        for post in candidates
    ]
    matches = [row for row in rows if row["match"]]
    result = {"ok": len(matches) == 1, "url": url, "slug": slug,
              "public_status": status, "public_bytes": len(body), "candidates": rows}
    if len(matches) == 1:
        match = matches[0]
        result.update({"post_id": match["post_id"], "modified": match["modified"]})
    else:
        result["error"] = f"{len(matches)} candidates have a link equal to the URL; expected exactly 1"
    return result


def main():
    parser = argparse.ArgumentParser(description="Resolve a public URL to its WordPress post by exact link match")
    parser.add_argument("url", help="public URL of the live post")
    args = parser.parse_args()
    result = resolve(args.url)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
