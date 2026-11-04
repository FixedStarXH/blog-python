"""Exercise Go-schema import with two isolated databases."""
import json
import os
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from pwdlib.hashers.bcrypt import BcryptHasher


class GoImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="blog-go-import-")
        root = Path(self.tmp.name)
        self.source_path = root / "go.db"
        self.target_path = root / "python.db"
        self.source_url = f"sqlite:///{self.source_path.as_posix()}"
        self.target_url = f"sqlite:///{self.target_path.as_posix()}"
        with closing(sqlite3.connect(self.source_path)) as db:
            db.executescript("""
                CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT NOT NULL, email TEXT NOT NULL, password TEXT NOT NULL, role INTEGER, status INTEGER);
                CREATE TABLE categories (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE articles (id INTEGER PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL, author_id INTEGER NOT NULL, category_id INTEGER NOT NULL, status INTEGER, password TEXT, view_count INTEGER, like_count INTEGER);
                CREATE TABLE article_tags (article_id INTEGER, tag_id INTEGER);
                CREATE TABLE comments (id INTEGER PRIMARY KEY, article_id INTEGER, user_id INTEGER, parent_id INTEGER, content TEXT, nickname TEXT, status INTEGER);
                CREATE TABLE settings (k TEXT PRIMARY KEY, v TEXT);
                CREATE TABLE article_views (article_id INTEGER, ip TEXT, viewed_at DATETIME, deleted_at DATETIME);
                CREATE TABLE article_likes (article_id INTEGER, ip TEXT, user_id INTEGER, created_at DATETIME, deleted_at DATETIME);
                CREATE TABLE article_chunks (id INTEGER PRIMARY KEY, article_id INTEGER, chunk_index INTEGER, content TEXT, embedding BLOB, embedding_model TEXT, embedding_dim INTEGER);
            """)
            bcrypt_hash = BcryptHasher(rounds=4, prefix="2a").hash("old-password")
            db.execute("INSERT INTO users VALUES (1,?,?,?,?,?)", ("legacy", "legacy@example.test", bcrypt_hash, 3, 1))
            db.execute("INSERT INTO categories VALUES (1, 'General')")
            db.execute("INSERT INTO tags VALUES (1, 'Python')")
            db.execute("INSERT INTO articles VALUES (1, 'Public', 'public content', 1, 1, 1, '', 12, 1)")
            db.execute("INSERT INTO articles VALUES (2, 'Private', 'secret content', 1, 1, 1, 'old-secret', 0, 0)")
            db.execute("INSERT INTO article_tags VALUES (1, 1)")
            db.execute("INSERT INTO comments VALUES (1, 1, 1, NULL, 'parent', 'legacy', 1)")
            db.execute("INSERT INTO comments VALUES (2, 1, 1, 1, 'reply', 'legacy', 1)")
            db.execute("INSERT INTO settings VALUES ('site_title', 'Old site')")
            db.execute("INSERT INTO article_views VALUES (1, '127.0.0.1', '2026-01-01 00:00:00', NULL)")
            db.execute("INSERT INTO article_views VALUES (1, '127.0.0.1', '2026-01-02 00:00:00', NULL)")
            db.execute("INSERT INTO article_likes VALUES (1, '127.0.0.1', 1, '2026-01-01 00:00:00', NULL)")
            db.execute("INSERT INTO article_chunks VALUES (1, 1, 0, 'public chunk', ?, 'legacy-model', 2)", (struct.pack('<ff', 1.0, 0.5),))
            db.execute("INSERT INTO article_chunks VALUES (2, 2, 0, 'private chunk', ?, 'legacy-model', 2)", (struct.pack('<ff', 1.0, 0.5),))
            db.commit()
        env = os.environ.copy()
        env["BLOG_DATABASE_URL"] = self.target_url
        env["BLOG_UPLOAD_DIR"] = str(root / "uploads")
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, env=env, capture_output=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_and_atomic_import(self):
        from app.core.security import verify_password
        from scripts.import_go import ImportErrorWithContext, import_database
        preview = import_database(self.source_url, self.target_url)
        self.assertFalse(preview["applied"])
        self.assertEqual(preview["skipped"]["article_views_duplicate"], 1)
        self.assertEqual(preview["skipped"]["article_chunks_nonpublic"], 1)
        with closing(sqlite3.connect(self.target_path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)

        applied = import_database(self.source_url, self.target_url, apply=True)
        self.assertTrue(applied["applied"])
        with closing(sqlite3.connect(self.target_path)) as db:
            password_hash = db.execute("SELECT password_hash FROM users WHERE id=1").fetchone()[0]
            article_password = db.execute("SELECT password FROM articles WHERE id=2").fetchone()[0]
            self.assertTrue(verify_password("old-password", password_hash))
            self.assertTrue(verify_password("old-secret", article_password))
            self.assertTrue(article_password.startswith("$argon2"))
            self.assertEqual(db.execute("SELECT parent_id FROM comments WHERE id=2").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM article_metrics").fetchone()[0], 2)
            self.assertEqual(json.loads(db.execute("SELECT embedding FROM article_chunks WHERE id=1").fetchone()[0]), [1.0, 0.5])
            self.assertEqual(db.execute("SELECT value FROM settings WHERE `key`='site_title'").fetchone()[0], "Old site")
        with self.assertRaises(ImportErrorWithContext):
            import_database(self.source_url, self.target_url, apply=True)

    def test_same_database_is_rejected(self):
        from scripts.import_go import ImportErrorWithContext, import_database
        with self.assertRaises(ImportErrorWithContext):
            import_database(self.source_url, self.source_url, apply=True)

    def test_insert_failure_rolls_back_all_tables(self):
        from scripts.import_go import ImportErrorWithContext, import_database
        with closing(sqlite3.connect(self.source_path)) as db:
            db.execute("INSERT INTO article_tags VALUES (1, 1)")
            db.commit()
        with self.assertRaises(ImportErrorWithContext):
            import_database(self.source_url, self.target_url, apply=True)
        with closing(sqlite3.connect(self.target_path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM articles").fetchone()[0], 0)

    def test_duplicate_names_are_rejected_before_writing(self):
        from scripts.import_go import ImportErrorWithContext, import_database
        with closing(sqlite3.connect(self.source_path)) as db:
            password_hash = BcryptHasher(rounds=4).hash("another-password")
            db.execute("INSERT INTO users VALUES (2,?,?,?,?,?)", ("LEGACY", "another@example.test", password_hash, 1, 1))
            db.commit()
        with self.assertRaisesRegex(ImportErrorWithContext, "重复值"):
            import_database(self.source_url, self.target_url, apply=True)
        with closing(sqlite3.connect(self.target_path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)

    def test_outdated_destination_is_rejected(self):
        from scripts.import_go import ImportErrorWithContext, import_database
        with closing(sqlite3.connect(self.target_path)) as db:
            db.execute("UPDATE alembic_version SET version_num='0001_initial'")
            db.commit()
        with self.assertRaisesRegex(ImportErrorWithContext, "版本不是最新"):
            import_database(self.source_url, self.target_url, apply=True)
