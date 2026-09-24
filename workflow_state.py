import json
import os
import re
import socket
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path


class PublishLockError(RuntimeError):
    """Raised when another process owns a post's publishing lock."""


def _safe_slug(value):
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "post").lower()).strip("-")
    return slug or "post"


def _validate_post_id(post_id):
    if not isinstance(post_id, int) or isinstance(post_id, bool) or post_id <= 0:
        raise ValueError("post_id must be a positive integer")
    return post_id


def post_workspace(workspace_root, post_id, slug, create=True):
    """Return the isolated mutable workspace for one WordPress post."""
    post_id = _validate_post_id(post_id)
    path = Path(workspace_root) / "work" / f"post-{post_id}-{_safe_slug(slug)}"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def protected_keywords_path(workspace_root, post_id, slug, create=True):
    return post_workspace(workspace_root, post_id, slug, create=create) / "protected-keywords.md"


def write_protected_keywords(workspace_root, post_id, slug, keywords):
    """Atomically replace one post's protected-keyword file."""
    target = protected_keywords_path(workspace_root, post_id, slug)
    normalized = [str(keyword).strip() for keyword in keywords if str(keyword).strip()]
    content = "".join(f"{keyword}\n" for keyword in normalized)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def _read_lock_owner(lock_dir):
    try:
        return json.loads((lock_dir / "owner.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"detail": "owner metadata unavailable"}


@contextmanager
def publish_lock(workspace_root, post_id, canonical_url=None):
    """Exclusively lock a post from the final fresh fetch through verification."""
    post_id = _validate_post_id(post_id)
    lock_root = Path(workspace_root) / "work" / ".publish-locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_dir = lock_root / f"post-{post_id}"
    token = uuid.uuid4().hex
    owner = {
        "schema_version": 1,
        "post_id": post_id,
        "canonical_url": canonical_url,
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "acquired_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "token": token,
    }

    try:
        lock_dir.mkdir()
    except FileExistsError as exc:
        current_owner = _read_lock_owner(lock_dir)
        raise PublishLockError(
            f"Post {post_id} is already locked for publishing: "
            f"{json.dumps(current_owner, ensure_ascii=False, sort_keys=True)}"
        ) from exc

    try:
        (lock_dir / "owner.json").write_text(
            json.dumps(owner, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except BaseException:
        owner_file = lock_dir / "owner.json"
        if owner_file.exists():
            owner_file.unlink()
        lock_dir.rmdir()
        raise

    try:
        yield lock_dir
    finally:
        current_owner = _read_lock_owner(lock_dir)
        if current_owner.get("token") == token:
            (lock_dir / "owner.json").unlink()
            lock_dir.rmdir()


@contextmanager
def publish_locks(workspace_root, posts):
    """Acquire a batch of post locks in deterministic post-ID order."""
    normalized = {}
    for post_id, canonical_url in posts:
        post_id = _validate_post_id(post_id)
        if post_id in normalized and normalized[post_id] != canonical_url:
            raise ValueError(f"Conflicting canonical URLs supplied for post {post_id}")
        normalized[post_id] = canonical_url

    with ExitStack() as stack:
        acquired = {
            post_id: stack.enter_context(publish_lock(workspace_root, post_id, normalized[post_id]))
            for post_id in sorted(normalized)
        }
        yield acquired


def locked_publisher(workspace_root, post_id, canonical_url=None):
    """Decorate a page-specific publisher so its entire run owns the post lock."""
    def decorator(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with publish_lock(workspace_root, post_id, canonical_url):
                return function(*args, **kwargs)

        return wrapped

    return decorator
