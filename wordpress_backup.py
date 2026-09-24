import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


BACKUP_NAME_RE = re.compile(
    r"^(?P<timestamp>\d{8}T\d{12}Z)-post-(?P<post_id>\d+)-(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)$"
)


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _safe_slug(value):
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "post").lower()).strip("-")
    return slug or "post"


def create_backup(backup_root, post, api_url, restore_fields):
    """Create a complete, checksummed backup before a WordPress write."""
    backup_root = Path(backup_root)
    post_id = post.get("id")
    if not isinstance(post_id, int):
        raise RuntimeError("Cannot back up a post without an integer id")
    if post.get("status") != "publish":
        raise RuntimeError("Cannot back up a post that is not published")
    if not post.get("link"):
        raise RuntimeError("Cannot back up a post without a canonical link")

    restore_fields = tuple(restore_fields)
    missing = [field for field in restore_fields if field not in post]
    if missing:
        raise RuntimeError(f"Cannot back up missing restore fields: {', '.join(missing)}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_name = f"{timestamp}-post-{post_id}-{_safe_slug(post.get('slug'))}"
    backup_dir = backup_root / backup_name
    backup_dir.mkdir(parents=True, exist_ok=False)

    snapshot_bytes = _json_bytes(post)
    restore_payload = {field: post[field] for field in restore_fields}
    payload_bytes = _json_bytes(restore_payload)
    manifest = {
        "schema_version": 1,
        "backup_name": backup_name,
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "post_id": post_id,
        "slug": post.get("slug"),
        "canonical_url": post["link"],
        "api_url": api_url,
        "status": post["status"],
        "restore_fields": list(restore_fields),
        "files": {
            "post.json": {"sha256": _sha256(snapshot_bytes)},
            "restore-payload.json": {"sha256": _sha256(payload_bytes)},
        },
    }

    (backup_dir / "post.json").write_bytes(snapshot_bytes)
    (backup_dir / "restore-payload.json").write_bytes(payload_bytes)
    (backup_dir / "manifest.json").write_bytes(_json_bytes(manifest))
    load_and_verify_backup(backup_dir)
    return backup_dir


def load_and_verify_backup(backup_dir):
    """Load a backup only after validating its name, manifest, and checksums."""
    backup_dir = Path(backup_dir)
    name_match = BACKUP_NAME_RE.fullmatch(backup_dir.name)
    if not name_match:
        raise RuntimeError(f"Non-standard backup folder name: {backup_dir.name}")

    manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise RuntimeError("Unsupported backup schema version")
    if manifest.get("backup_name") != backup_dir.name:
        raise RuntimeError("Backup folder name does not match its manifest")
    if int(name_match.group("post_id")) != manifest.get("post_id"):
        raise RuntimeError("Backup folder post id does not match its manifest")

    loaded = {}
    for filename in ("post.json", "restore-payload.json"):
        data = (backup_dir / filename).read_bytes()
        expected = manifest.get("files", {}).get(filename, {}).get("sha256")
        if not expected or _sha256(data) != expected:
            raise RuntimeError(f"Backup checksum failed for {filename}")
        loaded[filename] = json.loads(data.decode("utf-8"))

    payload = loaded["restore-payload.json"]
    if sorted(payload) != sorted(manifest.get("restore_fields", [])):
        raise RuntimeError("Restore payload fields do not match the manifest")
    return manifest, loaded["post.json"], payload
