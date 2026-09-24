import argparse
import json
import urllib.parse
from pathlib import Path

from wordpress_backup import create_backup, load_and_verify_backup
from workflow_state import publish_lock
from wp_client import basic_auth, ensure_writes_enabled, load_env, request_json


HERE = Path(__file__).resolve().parent
BAK = HERE / "bak"


def resolve_backup(value):
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = BAK / candidate
    resolved = candidate.resolve()
    if resolved.parent != BAK.resolve():
        raise RuntimeError("Backup must be a direct child of the bak folder")
    return resolved


def validate_live_target(current, manifest):
    if current.get("id") != manifest["post_id"]:
        raise RuntimeError("Live post id does not match the backup")
    if current.get("link") != manifest["canonical_url"]:
        raise RuntimeError("Live canonical URL does not match the backup")
    if current.get("status") != "publish" or manifest.get("status") != "publish":
        raise RuntimeError("Both the live post and backup must have publish status")


def main():
    parser = argparse.ArgumentParser(description="Validate or restore a WordPress backup")
    parser.add_argument("backup", help="standard backup folder name, or its path under bak/")
    parser.add_argument("--confirm-post-id", required=True, type=int)
    parser.add_argument("--restore", action="store_true", help="perform the restore; default is validation only")
    parser.add_argument("--username-env", default="WP_USERNAME_VS")
    parser.add_argument("--password-env", default="WP_APP_PASSWORD_VS")
    args = parser.parse_args()

    backup_dir = resolve_backup(args.backup)
    manifest, snapshot, payload = load_and_verify_backup(backup_dir)
    if manifest["post_id"] != args.confirm_post_id:
        raise RuntimeError("Confirmed post id does not match the backup")
    if snapshot.get("id") != manifest["post_id"] or snapshot.get("link") != manifest["canonical_url"]:
        raise RuntimeError("Snapshot identity does not match the backup manifest")

    env = load_env()
    authorization = basic_auth(env, args.username_env, args.password_env)
    api_url = manifest["api_url"]
    api_parts = urllib.parse.urlsplit(api_url)
    canonical_parts = urllib.parse.urlsplit(manifest["canonical_url"])
    if api_parts.scheme != "https" or api_parts.hostname != canonical_parts.hostname:
        raise RuntimeError("Backup API endpoint is not HTTPS on the canonical site host")
    if api_parts.username or api_parts.password:
        raise RuntimeError("Backup API endpoint must not contain credentials")
    if not args.restore:
        current = request_json(f"{api_url}?context=edit", authorization)
        validate_live_target(current, manifest)
        print(json.dumps({"ok": True, "mode": "validate", "backup": str(backup_dir), "post_id": manifest["post_id"], "restore_fields": manifest["restore_fields"]}, indent=2))
        return
    ensure_writes_enabled()

    with publish_lock(HERE, manifest["post_id"], manifest["canonical_url"]):
        current = request_json(f"{api_url}?context=edit", authorization)
        validate_live_target(current, manifest)
        safety_backup = create_backup(BAK, current, api_url, manifest["restore_fields"])
        response = request_json(api_url, authorization, method="POST", payload=payload)
        for field, expected in payload.items():
            if response.get(field) != expected:
                raise RuntimeError(f"WordPress restore response does not match field {field}")
        live_after = request_json(f"{api_url}?context=edit", authorization)
        for field, expected in payload.items():
            if live_after.get(field) != expected:
                raise RuntimeError(f"WordPress restore readback does not match field {field}")
        if live_after.get("status") != "publish" or live_after.get("link") != manifest["canonical_url"]:
            raise RuntimeError("Restored post is not live at the expected canonical URL")
    print(json.dumps({"ok": True, "mode": "restore", "backup": str(backup_dir), "safety_backup": str(safety_backup), "post_id": manifest["post_id"], "restored_fields": manifest["restore_fields"]}, indent=2))


if __name__ == "__main__":
    main()
