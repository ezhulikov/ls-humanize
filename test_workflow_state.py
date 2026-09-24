import tempfile
import unittest
from pathlib import Path

from workflow_state import (
    PublishLockError,
    locked_publisher,
    post_workspace,
    protected_keywords_path,
    publish_lock,
    publish_locks,
    write_protected_keywords,
)


class WorkflowStateTests(unittest.TestCase):
    def test_post_workspaces_are_isolated(self):
        with tempfile.TemporaryDirectory() as root:
            first = post_workspace(root, 101, "first-page")
            second = post_workspace(root, 202, "second-page")
            self.assertNotEqual(first, second)
            self.assertTrue(first.is_dir())
            self.assertTrue(second.is_dir())

    def test_keyword_files_do_not_collide(self):
        with tempfile.TemporaryDirectory() as root:
            first = write_protected_keywords(root, 101, "first-page", ["alpha", "beta"])
            second = write_protected_keywords(root, 202, "second-page", ["gamma"])
            self.assertEqual(first.read_text(encoding="utf-8"), "alpha\nbeta\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "gamma\n")
            self.assertEqual(first, protected_keywords_path(root, 101, "first-page"))

    def test_same_post_cannot_be_locked_twice(self):
        with tempfile.TemporaryDirectory() as root:
            with publish_lock(root, 101, "https://example.com/first/"):
                with self.assertRaises(PublishLockError):
                    with publish_lock(root, 101, "https://example.com/first/"):
                        pass

    def test_different_posts_can_be_locked_together(self):
        with tempfile.TemporaryDirectory() as root:
            with publish_lock(root, 101), publish_lock(root, 202):
                lock_root = Path(root) / "work" / ".publish-locks"
                self.assertTrue((lock_root / "post-101").is_dir())
                self.assertTrue((lock_root / "post-202").is_dir())
            self.assertFalse((lock_root / "post-101").exists())
            self.assertFalse((lock_root / "post-202").exists())

    def test_post_id_must_be_positive_integer(self):
        with tempfile.TemporaryDirectory() as root:
            for value in (0, -1, True, "101"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    post_workspace(root, value, "page")

    def test_locked_publisher_releases_after_failure(self):
        with tempfile.TemporaryDirectory() as root:
            @locked_publisher(root, 101, "https://example.com/first/")
            def fail():
                raise RuntimeError("expected")

            with self.assertRaisesRegex(RuntimeError, "expected"):
                fail()
            with publish_lock(root, 101):
                pass

    def test_batch_lock_failure_releases_locks_already_acquired(self):
        with tempfile.TemporaryDirectory() as root:
            with publish_lock(root, 202):
                with self.assertRaises(PublishLockError):
                    with publish_locks(root, [(202, None), (101, None)]):
                        pass
                with publish_lock(root, 101):
                    pass


if __name__ == "__main__":
    unittest.main()
