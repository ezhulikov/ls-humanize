"""Full cache purge for one post, ported from the vs publish_guarded.py transport.

The site's edge cache (Cloudflare) can keep serving a pre-write page long after
a verified WordPress write, because no local Cloudflare credential exists. The
vs workflows solve this by deploying a temporary Code Snippets PHP snippet and
triggering it once with a token. Running inside WordPress, the snippet:

1. Purges LiteSpeed (`Purge::purge_all`, `purge_all_lscache`) and fires the
   `litespeed_purge_all` / `litespeed_purge_url` actions.
2. Reads the Cloudflare credentials stored in WordPress options (the Cloudflare
   plugin's or LiteSpeed's CDN settings) and calls the Cloudflare API to purge
   the post's URLs and then the whole zone.
3. Deletes its own snippet row and returns a JSON result.

Afterwards this module verifies the public page with real-browser user agents
and reports `CF-Cache-Status`/`Age` per variant plus optional content checks.

Deploying the snippet is a remote write and therefore requires
WP_ENABLE_WRITES=true. A purge failure degrades to a reported error; leftover
snippets are cleaned up with a delete-and-recheck pass because the Code
Snippets DELETE endpoint has returned 204 without removing rows before.

Usage:
    $env:WP_ENABLE_WRITES="true"
    python purge_cache.py --post-id <id> --url <canonical-url> [--must-contain "text"]...
"""

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from wp_client import basic_auth, ensure_writes_enabled, load_env, request_json

BASE = "https://venturesmarter.com"
SNIPPETS_API = f"{BASE}/wp-json/code-snippets/v1/snippets"
SNIPPET_NAME_PREFIX = "ld_humanize_purge_"
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
OPERA_UA = CHROME_UA + " OPR/112.0.0.0"


def php_string(value):
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def build_purge_php(name, token, post_id, urls):
    php_urls = ",\n        ".join(php_string(url) for url in urls)
    return f"""
add_action('init', function () {{
    if (!isset($_GET['ld_task']) || $_GET['ld_task'] !== {php_string(token)}) return;
    $urls = array({php_urls});
    $results = array('litespeed' => array(), 'cloudflare_files' => null, 'cloudflare_everything' => null);
    if (class_exists('LiteSpeed\\Purge')) {{
        try {{ LiteSpeed\\Purge::purge_all('ld humanize publish'); $results['litespeed'][] = 'Purge::purge_all'; }} catch (Throwable $e) {{ $results['litespeed'][] = 'Purge::purge_all error: ' . $e->getMessage(); }}
        try {{ LiteSpeed\\Purge::purge_all_lscache('ld humanize publish'); $results['litespeed'][] = 'Purge::purge_all_lscache'; }} catch (Throwable $e) {{ $results['litespeed'][] = 'Purge::purge_all_lscache error: ' . $e->getMessage(); }}
    }}
    if (class_exists('LiteSpeed\\CDN\\Cloudflare')) {{
        try {{ LiteSpeed\\CDN\\Cloudflare::purge_all('ld humanize publish'); $results['litespeed'][] = 'Cloudflare::purge_all'; }} catch (Throwable $e) {{ $results['litespeed'][] = 'Cloudflare::purge_all error: ' . $e->getMessage(); }}
    }}
    do_action('litespeed_purge_all');
    $results['litespeed'][] = 'action:litespeed_purge_all';
    foreach ($urls as $url) {{ do_action('litespeed_purge_url', $url); }}
    $results['litespeed'][] = 'action:litespeed_purge_url';
    clean_post_cache({post_id});
    $results['litespeed'][] = 'clean_post_cache({post_id})';
    $email = get_option('cloudflare_api_email') ?: get_option('litespeed.conf.cdn-cloudflare_email');
    $key = get_option('cloudflare_api_key') ?: get_option('litespeed.conf.cdn-cloudflare_key');
    $zone = get_option('litespeed.conf.cdn-cloudflare_zone');
    $domain = get_option('cloudflare_cached_domain_name') ?: get_option('litespeed.conf.cdn-cloudflare_name') ?: 'venturesmarter.com';
    $headers = array('X-Auth-Email' => $email, 'X-Auth-Key' => $key, 'Content-Type' => 'application/json');
    if (!$zone && $email && $key) {{
        $zone_resp = wp_remote_get('https://api.cloudflare.com/client/v4/zones?name=' . rawurlencode($domain), array('headers' => $headers, 'timeout' => 30));
        $zone_body = json_decode(wp_remote_retrieve_body($zone_resp), true);
        $zone = $zone_body['result'][0]['id'] ?? '';
    }}
    if ($zone && $email && $key) {{
        $files_resp = wp_remote_post('https://api.cloudflare.com/client/v4/zones/' . rawurlencode($zone) . '/purge_cache', array('headers' => $headers, 'timeout' => 30, 'body' => wp_json_encode(array('files' => $urls))));
        $results['cloudflare_files'] = array('code' => wp_remote_retrieve_response_code($files_resp));
        $everything_resp = wp_remote_post('https://api.cloudflare.com/client/v4/zones/' . rawurlencode($zone) . '/purge_cache', array('headers' => $headers, 'timeout' => 30, 'body' => wp_json_encode(array('purge_everything' => true))));
        $results['cloudflare_everything'] = array('code' => wp_remote_retrieve_response_code($everything_resp));
    }}
    global $wpdb;
    $results['deleted_self_rows'] = $wpdb->query($wpdb->prepare("DELETE FROM {{$wpdb->prefix}}snippets WHERE name = %s", {php_string(name)}));
    wp_send_json($results);
}});
""".strip()


def fetch_variant(url, user_agent):
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.read().decode("utf-8", "replace"), dict(response.headers), response.status


def variant_report(url, stamp, must_contain):
    variants = {}
    busted = url + ("&" if "?" in url else "?") + "ld_verify=" + stamp
    for label, target, user_agent in (
        ("chrome", url, CHROME_UA),
        ("opera", url, OPERA_UA),
        ("cache_busted", busted, CHROME_UA),
    ):
        body, headers, status = fetch_variant(target, user_agent)
        variants[label] = {
            "status": status,
            "bytes": len(body.encode("utf-8")),
            "cf_cache_status": headers.get("CF-Cache-Status"),
            "age": headers.get("Age"),
            "missing": [text for text in must_contain if text not in body],
        }
    return variants


def cleanup_snippets(authorization, name):
    """Delete leftover snippet rows and re-check; DELETE has silently no-opped before."""
    removed = []
    for _ in range(2):
        rows = request_json(f"{SNIPPETS_API}?per_page=100", authorization)
        leftovers = [row.get("id") for row in rows if isinstance(row, dict) and row.get("name") == name]
        if not leftovers:
            return removed, []
        for snippet_id in leftovers:
            try:
                request_json(f"{SNIPPETS_API}/{snippet_id}", authorization, method="DELETE")
                removed.append(snippet_id)
            except Exception as error:  # noqa: BLE001 - reported, not fatal
                removed.append(f"{snippet_id}: {error}")
        time.sleep(3)
    rows = request_json(f"{SNIPPETS_API}?per_page=100", authorization)
    remaining = [row.get("id") for row in rows if isinstance(row, dict) and row.get("name") == name]
    return removed, remaining


def purge_post_cache(authorization, post_id, url, must_contain=(), settle_seconds=10):
    """Deploy, trigger, and clean up a one-shot purge snippet, then verify variants."""
    ensure_writes_enabled()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    token = "tok_" + stamp + "_" + os.urandom(8).hex()
    name = SNIPPET_NAME_PREFIX + stamp
    bare = url.rstrip("/")
    urls = [url, bare, url.replace("https://", "http://"), bare.replace("https://", "http://")]
    report = {"post_id": post_id, "url": url, "snippet": name, "ok": False}
    try:
        created = request_json(
            SNIPPETS_API,
            authorization,
            method="POST",
            payload={
                "name": name,
                "code": build_purge_php(name, token, post_id, urls),
                "scope": "global",
                "active": True,
                "priority": 10,
            },
        )
        report["snippet_id"] = created.get("id") if isinstance(created, dict) else None
        query = urllib.parse.urlencode({"ld_task": token, "nocache": stamp})
        report["trigger"] = request_json(f"{BASE}/wp-json/?{query}", authorization)
        trigger = report["trigger"]
        report["cloudflare_purged"] = (
            isinstance(trigger, dict) and (trigger.get("cloudflare_everything") or {}).get("code") == 200
        )
        time.sleep(settle_seconds)
    except Exception as error:  # noqa: BLE001 - purge must degrade, never crash a publish
        report["error"] = str(error)
    finally:
        try:
            report["removed_snippets"], report["leftover_snippets"] = cleanup_snippets(authorization, name)
        except Exception as error:  # noqa: BLE001
            report["cleanup_error"] = str(error)
    try:
        report["variants"] = variant_report(url, stamp, list(must_contain))
        plain = report["variants"]["chrome"]
        report["stale"] = bool(
            plain["cf_cache_status"] == "HIT" and int(plain["age"] or 0) > 120
        ) or bool(plain["missing"])
        report["ok"] = "error" not in report and not report["stale"] and not report.get("leftover_snippets")
    except Exception as error:  # noqa: BLE001
        report["verify_error"] = str(error)
    return report


def main():
    parser = argparse.ArgumentParser(description="Purge LiteSpeed and Cloudflare caches for one post and verify the public page")
    parser.add_argument("--post-id", required=True, type=int)
    parser.add_argument("--url", required=True, help="canonical public URL of the live post")
    parser.add_argument("--must-contain", action="append", default=[],
                        help="text that must appear in every public variant after the purge (repeatable)")
    args = parser.parse_args()
    url = args.url if args.url.endswith("/") else args.url + "/"
    authorization = basic_auth(load_env())
    report = purge_post_cache(authorization, args.post_id, url, args.must_contain)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
