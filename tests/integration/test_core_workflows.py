"""Integration checks against a migrated, isolated SQLite database."""
import os
import json
import asyncio
from io import BytesIO
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient


class CoreWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="blog-integration-")
        root = Path(cls.tmp.name)
        cls.env = patch.dict(os.environ, {
            "BLOG_DATABASE_URL": f"sqlite:///{(root / 'test.db').as_posix()}",
            "BLOG_UPLOAD_DIR": str(root / "uploads"),
            "BLOG_JWT_SECRET": "integration-test-secret-at-least-32-bytes",
        })
        cls.env.start()
        config = Config("alembic.ini")
        command.upgrade(config, "head")

        from app.db.seed import seed
        from app.db.session import SessionLocal
        with SessionLocal() as db:
            seed(db)
            seed(db)

        from app.main import app
        cls.app = app
        cls.limiter = patch("app.main.enforce_rate_limit", new=AsyncMock(return_value=None))
        cls.limiter.start()
        cls.client = TestClient(app)
        cls.client.__enter__()
        cls.redis = app.state.redis
        app.state.redis = None

    @classmethod
    def tearDownClass(cls):
        cls.app.state.redis = cls.redis
        cls.client.__exit__(None, None, None)
        cls.limiter.stop()
        cls.env.stop()
        cls.tmp.cleanup()

    def test_auth_submission_review_and_private_article(self):
        client = self.client
        registered = client.post("/api/auth/register", json={"username": "author1", "email": "author1@example.test", "password": "password123"})
        self.assertEqual(registered.status_code, 201, registered.text)
        tokens = registered.json()["data"]
        author = {"Authorization": "Bearer " + tokens["accessToken"]}
        self.assertEqual(client.get("/api/admin/articles", headers=author).status_code, 403)
        self.assertEqual(client.get("/api/admin/articles").status_code, 401)

        rotated = client.post("/api/auth/refresh", json={"refreshToken": tokens["refreshToken"]})
        self.assertEqual(rotated.status_code, 200, rotated.text)
        self.assertEqual(client.post("/api/auth/refresh", json={"refreshToken": tokens["refreshToken"]}).status_code, 401)

        admin = client.post("/api/admin/login", json={"username": "admin", "password": "123456"})
        self.assertEqual(admin.status_code, 200, admin.text)
        editor = {"Authorization": "Bearer " + admin.json()["data"]["accessToken"]}
        created = client.post("/api/my/articles", headers=author, json={"title": "Private post", "content": "private body", "categoryId": 1, "password": "article-secret"})
        self.assertEqual(created.status_code, 201, created.text)
        article_id = created.json()["data"]["id"]
        self.assertEqual(client.get(f"/api/articles/{article_id}").status_code, 404)
        self.assertEqual(client.put(f"/api/admin/articles/{article_id}/approve", headers=editor).status_code, 200)
        detail = client.get(f"/api/articles/{article_id}").json()["data"]
        self.assertTrue(detail["needPassword"])
        self.assertNotIn("content", detail)
        self.assertEqual(client.post(f"/api/articles/{article_id}/unlock", json={"password": "wrong"}).status_code, 403)
        unlocked = client.post(f"/api/articles/{article_id}/unlock", json={"password": "article-secret"})
        self.assertEqual(unlocked.json()["data"]["content"], "private body")
        self.assertEqual(client.get(f"/api/articles/{article_id}/export").status_code, 404)
        self.assertEqual(client.delete(f"/api/my/articles/{article_id}", headers=author).status_code, 200)
        self.assertEqual(client.get(f"/api/articles/{article_id}").status_code, 404)

    def test_concurrent_registration_reports_duplicate(self):
        from app.services import auth
        original_hash = auth.hash_password
        barrier = threading.Barrier(2, timeout=10)

        def synchronized_hash(password):
            result = original_hash(password)
            barrier.wait()
            return result

        def register(email):
            return self.client.post("/api/auth/register", json={
                "username": "concurrent_author", "email": email, "password": "password123"
            })

        with patch("app.services.auth.hash_password", side_effect=synchronized_hash):
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(register, ["race_a@example.test", "race_b@example.test"]))
        self.assertEqual(sorted(response.status_code for response in responses), [201, 409])
        duplicate = next(response for response in responses if response.status_code == 409)
        self.assertEqual(duplicate.json()["code"], 409)

    def test_account_profile_password_and_refresh_contract(self):
        client = self.client
        invalid = client.post("/api/auth/register", json={
            "username": "invalid_email", "email": "not-an-email", "password": "abc123"
        })
        self.assertEqual(invalid.status_code, 422)
        response = client.post("/api/auth/register", json={
            "username": "account_contract", "email": "account_contract@example.test", "password": "abc123"
        })
        self.assertEqual(response.status_code, 201)
        tokens = response.json()["data"]
        headers = {"Authorization": "Bearer " + tokens["accessToken"]}
        profile = {"nickname": "New name", "avatar": "/uploads/profile.png", "bio": "About me"}
        updated = client.put("/api/auth/me", headers=headers, json=profile)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["data"], client.get("/api/auth/me", headers=headers).json()["data"])
        self.assertEqual(updated.json()["data"]["email"], "account_contract@example.test")
        self.assertEqual(updated.json()["data"]["role"], 1)
        self.assertEqual(client.put("/api/auth/password", headers=headers, json={
            "oldPassword": "wrong", "newPassword": "new123"
        }).status_code, 400)
        changed = client.put("/api/auth/password", headers=headers, json={
            "oldPassword": "abc123", "newPassword": "new123"
        })
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(client.post("/api/auth/refresh", json={"refreshToken": tokens["refreshToken"]}).status_code, 401)
        self.assertEqual(client.post("/api/auth/login", json={"username": "account_contract", "password": "abc123"}).status_code, 401)
        logged_in = client.post("/api/auth/login", json={"username": "account_contract@example.test", "password": "new123"})
        self.assertEqual(logged_in.status_code, 200)
        new_tokens = logged_in.json()["data"]
        new_headers = {"Authorization": "Bearer " + new_tokens["accessToken"]}
        self.assertEqual(client.post("/api/auth/logout", headers=new_headers, json={
            "refreshToken": new_tokens["refreshToken"]
        }).status_code, 200)
        self.assertEqual(client.post("/api/auth/refresh", json={"refreshToken": new_tokens["refreshToken"]}).status_code, 401)

    def test_submission_rejects_deleted_category_and_preserves_existing_article(self):
        from app.db.session import SessionLocal
        from app.models import Category
        with SessionLocal() as db:
            category = Category(name="Deleted submission category", deleted_at=datetime.utcnow())
            db.add(category)
            db.commit()
            deleted_id = category.id

        client = self.client
        tokens = client.post("/api/auth/register", json={
            "username": "submission_contract", "email": "submission_contract@example.test", "password": "password123"
        }).json()["data"]
        headers = {"Authorization": "Bearer " + tokens["accessToken"]}
        payload = {"title": "Original submission", "content": "original body", "categoryId": 1}
        created = client.post("/api/my/articles", headers=headers, json=payload)
        self.assertEqual(created.status_code, 201)
        article_id = created.json()["data"]["id"]
        invalid = {**payload, "title": "Changed", "categoryId": deleted_id}
        self.assertEqual(client.post("/api/my/articles", headers=headers, json=invalid).status_code, 400)
        self.assertEqual(client.put(f"/api/my/articles/{article_id}", headers=headers, json=invalid).status_code, 400)
        saved = client.get(f"/api/my/articles/{article_id}", headers=headers).json()["data"]
        self.assertEqual((saved["title"], saved["categoryId"], saved["status"]), ("Original submission", 1, 2))
        self.assertEqual(client.get(f"/api/my/articles/{article_id}").status_code, 401)
        self.assertEqual(client.get("/api/my/articles?status=-1", headers=headers).json()["data"]["total"], 1)

    def test_public_list_search_and_featured_paging(self):
        from app.db.session import SessionLocal
        from app.models import Article
        with SessionLocal() as db:
            rows = [Article(title=f"Card {n}", content="body-only-needle", author_id=1,
                            category_id=1, status=1, is_featured=True, view_count=n) for n in range(6)]
            db.add_all(rows)
            db.add(Article(title="Locked card", content="body-only-needle", author_id=1,
                           category_id=1, status=1, password="locked", is_featured=True))
            db.commit()
            ids = [row.id for row in rows]
        result = self.client.get("/api/articles", params={"keyword": "body-only-needle", "sort": "hot",
                                                          "categoryId": 1, "authorId": 1}).json()["data"]
        self.assertEqual(result["total"], len(ids))
        self.assertEqual([row["id"] for row in result["list"]], ids[::-1])
        for row in result["list"]:
            self.assertNotIn("content", row)
            self.assertFalse(row["needPassword"])
        params = {"featured": "true", "seed": 77, "keyword": "ignored-featured-filter"}
        full = self.client.get("/api/articles", params={**params, "pageSize": 50}).json()["data"]["list"]
        self.assertTrue(set(ids).issubset({row["id"] for row in full}))
        pages = []
        for page in (1, 2):
            data = self.client.get("/api/articles", params={**params, "page": page, "pageSize": 3}).json()["data"]
            self.assertEqual(data["page"], page)
            self.assertEqual(data["pageSize"], 3)
            pages.extend(data["list"])
        self.assertEqual(pages, full[:6])

    def test_public_detail_unlock_and_markdown_export_contract(self):
        from app.db.session import SessionLocal
        from app.models import Article
        html = ('<h2>Heading</h2><p><strong>Bold</strong> and <a href="/docs">Docs</a></p>'
                '<ul><li>Item</li></ul><pre><code class="language-python">a &lt; b</code></pre>'
                '<script>hidden-script</script>')
        with SessionLocal() as db:
            row = Article(title="Export contract", content=html, author_id=1, category_id=1, status=1)
            db.add(row)
            db.commit()
            path = f"/api/articles/{row.id}"
        detail = self.client.get(path).json()["data"]
        self.assertFalse(detail["needPassword"])
        self.assertNotIn("password", detail)
        self.assertIn("updatedAt", detail)
        self.assertIn("columnId", detail)
        unlocked = self.client.post(path + "/unlock", json={"password": "anything"})
        self.assertEqual(unlocked.json()["data"], detail)
        for password in ("", 123, None):
            self.assertEqual(self.client.post(path + "/unlock", json={"password": password}).status_code, 422)
        self.assertEqual(self.client.post("/api/articles/999999/unlock", json={"password": "any"}).status_code, 404)
        md = self.client.get(path + "/export").json()["data"]
        self.assertEqual(self.client.get(path + "/export?format=md").json()["data"], md)
        self.assertEqual((md["format"], md["title"]), ("md", detail["title"]))
        for fragment in ("## Heading", "**Bold**", "[Docs](/docs)", "- Item", "```python\na < b\n```"):
            self.assertIn(fragment, md["content"])
        self.assertNotIn("hidden-script", md["content"])
        invalid = self.client.get(path + "/export?format=pdf")
        self.assertEqual(invalid.status_code, 400)
        self.assertIsNone(invalid.json()["data"])

    def test_article_navigation_uses_category_time_and_public_visibility(self):
        from app.db.session import SessionLocal
        from app.models import Article, Category

        with SessionLocal() as db:
            category = Category(name="Navigation contract")
            other_category = Category(name="Other navigation category")
            db.add_all([category, other_category])
            db.flush()
            def make(title, day, **kwargs):
                return Article(title=title, content="body", author_id=1, category_id=category.id,
                               status=1, created_at=datetime(2024, 2, day), **kwargs)

            # Deliberately insert articles in a different order from their dates.
            current = make("Current", 10)
            newer = make("Newer", 12, view_count=7)
            older = make("Older", 8, view_count=13)
            private = make("Private", 9, password="locked", view_count=1000)
            draft = make("Draft", 11, view_count=999)
            draft.status = 0
            future = make("Future", 11, publish_at=datetime.utcnow() + timedelta(days=1))
            deleted = make("Deleted", 11, deleted_at=datetime.utcnow())
            other = Article(title="Other category", content="body", author_id=1,
                            category_id=other_category.id, status=1, created_at=datetime(2024, 2, 9))
            db.add_all([current, newer, older, private, draft, future, deleted, other])
            db.commit()
            current_id, newer_id, older_id = current.id, newer.id, older.id
            hidden_ids = {private.id, draft.id, future.id, deleted.id, other.id}

        nav = self.client.get(f"/api/articles/{current_id}/nav").json()["data"]
        self.assertEqual(nav["prev"]["id"], older_id)
        self.assertEqual(nav["next"]["id"], newer_id)
        self.assertEqual([item["id"] for item in nav["related"]], [older_id, newer_id])
        self.assertEqual([item["viewCount"] for item in nav["related"]], [13, 7])
        self.assertTrue(hidden_ids.isdisjoint({item["id"] for item in nav["related"]}))
        self.assertIsNone(self.client.get(f"/api/articles/{older_id}/nav").json()["data"]["prev"])
        self.assertIsNone(self.client.get(f"/api/articles/{newer_id}/nav").json()["data"]["next"])
        self.assertEqual(self.client.get(f"/api/articles/{future.id}/nav").status_code, 404)
        self.assertEqual(self.client.get("/api/articles/999999/nav").status_code, 404)

    def test_hot_and_archives_use_public_articles_and_expected_order(self):
        from app.db.session import SessionLocal
        from app.models import Article

        with SessionLocal() as db:
            popular = Article(title="Likes lead", content="body", author_id=1, category_id=1,
                              status=1, created_at=datetime(2001, 6, 1), like_count=10000, view_count=1)
            viewed = Article(title="Views lead", content="body", author_id=1, category_id=1,
                            status=1, created_at=datetime(2002, 6, 1), like_count=9999, view_count=10000)
            hidden = Article(title="Hidden hot", content="secret", author_id=1, category_id=1,
                             status=1, created_at=datetime(2003, 6, 1), password="secret", like_count=20000)
            db.add_all([popular, viewed, hidden])
            db.commit()
            popular_id, viewed_id, hidden_id = popular.id, viewed.id, hidden.id

        hot = self.client.get("/api/articles/hot?limit=2").json()["data"]
        self.assertEqual([item["id"] for item in hot], [popular_id, viewed_id])
        self.assertTrue(all("content" not in item for item in hot))
        groups = self.client.get("/api/archive").json()["data"]
        self.assertEqual(groups, self.client.get("/api/archives").json()["data"])
        selected = [group for group in groups if group["year"] in ("2001", "2002")]
        self.assertEqual([group["year"] for group in selected], ["2002", "2001"])
        self.assertEqual([group["articles"][0]["id"] for group in selected], [viewed_id, popular_id])
        self.assertTrue(all(group["count"] == len(group["articles"]) for group in groups))
        self.assertNotIn(hidden_id, {item["id"] for group in groups for item in group["articles"]})

    def test_future_article_is_hidden_across_public_endpoints(self):
        from app.db.session import SessionLocal
        from app.models import Article
        with SessionLocal() as db:
            article = Article(title="Future article", content="unpublished", author_id=1, category_id=1, status=1, publish_at=datetime.utcnow() + timedelta(days=1))
            db.add(article)
            db.commit()
            article_id = article.id

        client = self.client
        for suffix in ("", "/nav", "/export", "/comments"):
            with self.subTest(endpoint=suffix):
                self.assertEqual(client.get(f"/api/articles/{article_id}{suffix}").status_code, 404)
        self.assertEqual(client.post(f"/api/articles/{article_id}/summary").status_code, 404)
        self.assertEqual(client.post("/api/ai/ask", json={"question": "What?", "articleId": article_id}).status_code, 404)
        self.assertNotIn(article_id, [item["id"] for item in client.get("/api/articles").json()["data"]["list"]])

    def test_private_article_password_is_preserved_until_explicitly_cleared(self):
        client = self.client
        tokens = client.post("/api/auth/register", json={"username": "privacy_author", "email": "privacy_author@example.test", "password": "password123"}).json()["data"]
        author = {"Authorization": "Bearer " + tokens["accessToken"]}
        admin = client.post("/api/admin/login", json={"username": "admin", "password": "123456"}).json()["data"]
        editor = {"Authorization": "Bearer " + admin["accessToken"]}
        payload = {"title": "Private editing", "content": "secret body", "categoryId": 1, "password": "original-secret"}
        created = client.post("/api/my/articles", headers=author, json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        article_id = created.json()["data"]["id"]
        self.assertTrue(created.json()["data"]["hasPassword"])
        self.assertEqual(client.put(f"/api/admin/articles/{article_id}/approve", headers=editor).status_code, 200)
        payload["password"] = ""
        payload["title"] = "Edited by author"
        updated = client.put(f"/api/my/articles/{article_id}", headers=author, json=payload)
        self.assertTrue(updated.json()["data"]["hasPassword"])
        self.assertTrue(client.get(f"/api/articles/{article_id}").json()["data"]["needPassword"])
        self.assertEqual(client.post(f"/api/articles/{article_id}/unlock", json={"password": "original-secret"}).status_code, 200)
        updated = client.put(f"/api/admin/articles/{article_id}", headers=editor, json=payload)
        self.assertTrue(updated.json()["data"]["hasPassword"])
        self.assertEqual(client.post(f"/api/articles/{article_id}/unlock", json={"password": "original-secret"}).status_code, 200)
        conflict = client.put(f"/api/admin/articles/{article_id}", headers=editor, json={**payload, "password": "new-secret", "clearPassword": True})
        self.assertEqual(conflict.status_code, 400)
        self.assertTrue(client.get(f"/api/articles/{article_id}").json()["data"]["needPassword"])
        cleared = client.put(f"/api/admin/articles/{article_id}", headers=editor, json={**payload, "clearPassword": True})
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertFalse(cleared.json()["data"]["hasPassword"])
        self.assertEqual(client.get(f"/api/articles/{article_id}").json()["data"]["content"], "secret body")
        protected = client.put(f"/api/my/articles/{article_id}", headers=author, json={**payload, "password": "new-secret"})
        self.assertTrue(protected.json()["data"]["hasPassword"])
        cleared_by_author = client.put(f"/api/my/articles/{article_id}", headers=author, json={**payload, "clearPassword": True})
        self.assertFalse(cleared_by_author.json()["data"]["hasPassword"])

    def test_public_article_html_is_sanitized_including_legacy_and_private_content(self):
        from app.core.security import hash_password
        from app.db.session import SessionLocal
        from app.models import Article
        content = ('<h2>Safe heading</h2><p>Hello <strong>world</strong></p>'
                   '<pre><code class="language-python" onclick="evil()">print(1)</code></pre>'
                   '<a href="javascript:alert(1)">unsafe link</a>'
                   '<img src="/uploads/safe.png" onerror="alert(1)">'
                   '<svg onload="alert(1)"></svg><script>alert(1)</script>')
        with SessionLocal() as db:
            public = Article(title="Legacy HTML", content=content, author_id=1, category_id=1, status=1)
            private = Article(title="Private HTML", content=content, author_id=1, category_id=1, status=1, password=hash_password("secret-password"))
            db.add_all([public, private])
            db.commit()
            public_id, private_id = public.id, private.id
        client = self.client
        locked = client.get(f"/api/articles/{private_id}").json()["data"]
        self.assertTrue(locked["needPassword"])
        self.assertTrue(locked["hasPassword"])
        self.assertNotIn("content", locked)
        public_content = client.get(f"/api/articles/{public_id}").json()["data"]["content"]
        unlocked = client.post(f"/api/articles/{private_id}/unlock", json={"password": "secret-password"}).json()["data"]["content"]
        for result in (public_content, unlocked):
            self.assertIn("<h2>Safe heading</h2>", result)
            self.assertIn('<code class="language-python">', result)
            self.assertIn('src="/uploads/safe.png"', result)
            for forbidden in ("<script", "<svg", "onerror", "onclick", "javascript:"):
                self.assertNotIn(forbidden, result.lower())
        admin = client.post("/api/admin/login", json={"username": "admin", "password": "123456"}).json()["data"]
        editor = {"Authorization": "Bearer " + admin["accessToken"]}
        self.assertEqual(client.get(f"/api/admin/articles/{public_id}", headers=editor).json()["data"]["content"], content)

    def test_article_cache_does_not_reuse_unsanitized_legacy_entries(self):
        from app.db.session import SessionLocal
        from app.models import Article
        with SessionLocal() as db:
            article = Article(title="Cached legacy", content='<p>Safe</p><img src=x onerror="alert(1)">', author_id=1, category_id=1, status=1)
            db.add(article)
            db.commit()
            article_id = article.id
        path = f"/api/articles/{article_id}"

        class Cache:
            def __init__(self):
                self.values = {f"blog:http:0:{path}?": json.dumps({"status": 200, "body": {"data": {"content": "<script>unsafe</script>"}}})}

            async def get(self, key):
                return self.values.get(key)

            async def set(self, key, value, ex=None):
                self.values[key] = value

        old_redis = self.app.state.redis
        self.app.state.redis = Cache()
        try:
            first = self.client.get(path)
            self.assertEqual(first.headers["X-Cache"], "MISS")
            self.assertNotIn("onerror", first.json()["data"]["content"])
            second = self.client.get(path)
            self.assertEqual(second.headers["X-Cache"], "HIT")
            self.assertEqual(second.json()["data"]["content"], first.json()["data"]["content"])
        finally:
            self.app.state.redis = old_redis

    def test_unknown_api_uses_json_for_read_and_write(self):
        for method in ("GET", "POST", "PUT", "DELETE"):
            with self.subTest(method=method):
                response = self.client.request(method, "/api/not-implemented")
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["code"], 404)

    def test_public_comment_moderation_and_author_isolation(self):
        client = self.client
        author = client.post("/api/auth/register", json={"username": "author2", "email": "author2@example.test", "password": "password123"}).json()["data"]
        other = client.post("/api/auth/register", json={"username": "author3", "email": "author3@example.test", "password": "password123"}).json()["data"]
        owner_headers = {"Authorization": "Bearer " + author["accessToken"]}
        other_headers = {"Authorization": "Bearer " + other["accessToken"]}
        admin = client.post("/api/admin/login", json={"username": "admin", "password": "123456"}).json()["data"]
        admin_headers = {"Authorization": "Bearer " + admin["accessToken"]}
        created = client.post("/api/my/articles", headers=owner_headers, json={"title": "Public post", "content": "Public body", "categoryId": 1})
        self.assertEqual(created.status_code, 201, created.text)
        article_id = created.json()["data"]["id"]
        self.assertEqual(client.get(f"/api/my/articles/{article_id}", headers=other_headers).status_code, 404)
        self.assertEqual(client.delete(f"/api/my/articles/{article_id}", headers=other_headers).status_code, 404)
        self.assertEqual(client.put(f"/api/admin/articles/{article_id}/approve", headers=admin_headers).status_code, 200)
        self.assertEqual(client.put(f"/api/articles/{article_id}/view").json()["data"]["viewCount"], 1)
        self.assertEqual(client.put(f"/api/articles/{article_id}/view").json()["data"]["viewCount"], 1)
        comment = client.post(f"/api/articles/{article_id}/comments", json={"content": "Hello", "nickname": "Visitor"})
        self.assertEqual(comment.status_code, 201, comment.text)
        self.assertEqual(comment.json()["code"], 200)
        comment_id = comment.json()["data"]["id"]
        self.assertEqual(client.get(f"/api/articles/{article_id}/comments").json()["data"]["total"], 0)
        self.assertEqual(client.put(f"/api/admin/comments/{comment_id}/approve", headers=admin_headers).status_code, 200)
        self.assertEqual(client.get(f"/api/articles/{article_id}/comments").json()["data"]["total"], 1)
        expected_link = f"/article.html?id={article_id}"
        self.assertIn(expected_link, client.get("/api/rss.xml").text)
        self.assertIn(expected_link, client.get("/sitemap.xml").text)
        self.assertEqual(client.put("/api/admin/settings", headers=admin_headers, json={"quotes": [{"content": "Test quote", "author": "Author"}]}).status_code, 200)
        self.assertEqual(client.get("/api/quote/random").json()["data"], {"content": "Test quote", "author": "Author"})

    def test_article_like_toggle_and_concurrent_requests(self):
        from sqlalchemy import func, select
        from app.db.session import SessionLocal
        from app.models import Article, MetricEvent
        with SessionLocal() as db:
            article = Article(title="Like toggle", content="body", author_id=1, category_id=1, status=1)
            db.add(article)
            db.commit()
            article_id = article.id
        path = f"/api/articles/{article_id}/like"
        first = self.client.put(path)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["data"], {"likeCount": 1, "already": False})
        second = self.client.put(path)
        self.assertEqual(second.json()["data"], {"likeCount": 0, "already": True})
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: self.client.put(path), range(8)))
        self.assertEqual([r.status_code for r in responses], [200] * 8)
        with SessionLocal() as db:
            self.assertEqual(db.get(Article, article_id).like_count, 0)
            count = db.scalar(select(func.count()).select_from(MetricEvent).where(MetricEvent.article_id == article_id, MetricEvent.kind == "like"))
            self.assertEqual(count, 0)

    def test_view_outbox_recovers_from_failure_without_double_counting(self):
        from sqlalchemy import event, select
        from app.db.session import SessionLocal, engine
        from app.models import Article, MetricEvent
        from app.services.view_counts import flush_pending_views
        with patch("app.tasks.scheduler.flush_pending_views", return_value=0):
            with SessionLocal() as db:
                article = Article(title="Buffered views", content="body", author_id=1, category_id=1, status=1)
                db.add(article)
                db.commit()
                article_id, updated = article.id, article.updated_at
            path = f"/api/articles/{article_id}/view"
            self.assertEqual(self.client.put(path).json()["data"]["viewCount"], 1)

            self.assertEqual(self.client.put(path).json()["data"]["viewCount"], 1)
            with SessionLocal() as db:
                self.assertEqual(db.get(Article, article_id).view_count, 0)
                record = db.scalar(select(MetricEvent).where(MetricEvent.article_id == article_id, MetricEvent.kind == "view"))
                self.assertFalse(record.counted)
                event_id = record.id

            def fail_update(conn, cursor, statement, parameters, context, executemany):
                if statement.lstrip().upper().startswith("UPDATE ARTICLES SET"):
                    raise RuntimeError("simulated database failure")

            event.listen(engine, "before_cursor_execute", fail_update)
            try:
                with self.assertRaisesRegex(RuntimeError, "simulated database failure"):
                    flush_pending_views([event_id])
            finally:
                event.remove(engine, "before_cursor_execute", fail_update)
            with SessionLocal() as db:
                self.assertEqual(db.get(Article, article_id).view_count, 0)
                self.assertFalse(db.scalar(select(MetricEvent.counted).where(MetricEvent.article_id == article_id, MetricEvent.kind == "view")))
            self.assertEqual(flush_pending_views([event_id]), 1)
            self.assertEqual(flush_pending_views([event_id]), 0)
            with SessionLocal() as db:
                article = db.get(Article, article_id)
                self.assertEqual(article.view_count, 1)
                self.assertEqual(article.updated_at, updated)
            self.assertEqual(self.client.put(path).json()["data"]["viewCount"], 1)

    def test_view_outbox_competing_workers_apply_events_once(self):
        from sqlalchemy import func, select
        from app.db.session import SessionLocal
        from app.models import Article, MetricEvent
        from app.services.view_counts import flush_pending_views
        with patch("app.tasks.scheduler.flush_pending_views", return_value=0):
            with SessionLocal() as db:
                article = Article(title="View workers", content="body", author_id=1, category_id=1, status=1)
                db.add(article)
                db.flush()
                article_id = article.id
                rows = [MetricEvent(article_id=article_id, kind="view", fingerprint=f"test-{i}", counted=False) for i in range(20)]
                db.add_all(rows)
                db.commit()
                ids = [row.id for row in rows]
            with ThreadPoolExecutor(max_workers=4) as pool:
                applied = list(pool.map(lambda _: flush_pending_views(ids), range(4)))
            self.assertEqual(sum(applied), 20)
            with SessionLocal() as db:
                self.assertEqual(db.get(Article, article_id).view_count, 20)
                self.assertEqual(db.scalar(select(func.count()).select_from(MetricEvent).where(MetricEvent.id.in_(ids), MetricEvent.counted.is_(False))), 0)

    def test_comment_nickname_and_moderation_contract(self):
        from app.db.session import SessionLocal
        from app.models import Article
        with SessionLocal() as db:
            article = Article(title="Comment contract", content="content", author_id=1, category_id=1, status=1)
            db.add(article)
            db.commit()
            article_id = article.id
        client = self.client
        tokens = client.post("/api/auth/register", json={"username": "comment_user", "email": "comment_user@example.test", "password": "password123"}).json()["data"]
        author = {"Authorization": "Bearer " + tokens["accessToken"]}
        admin = client.post("/api/admin/login", json={"username": "admin", "password": "123456"}).json()["data"]
        editor = {"Authorization": "Bearer " + admin["accessToken"]}
        path = f"/api/articles/{article_id}/comments"
        explicit = client.post(path, headers=author, json={"content": "first", "nickname": "Chosen name"})
        self.assertEqual(explicit.status_code, 201, explicit.text)
        comment = explicit.json()["data"]
        self.assertEqual(comment["nickname"], "Chosen name")
        fallback = client.post(path, headers=author, json={"content": "second", "nickname": ""})
        self.assertEqual(fallback.status_code, 201, fallback.text)
        self.assertEqual(fallback.json()["data"]["nickname"], "comment_user")
        guest = client.post(path, json={"content": "guest", "nickname": ""})
        self.assertEqual(guest.json()["data"]["nickname"], "游客")
        status_path = f"/api/admin/comments/{comment['id']}/status"
        for invalid in ({}, {"status": 9}):
            self.assertEqual(client.put(status_path, headers=editor, json=invalid).status_code, 422)
        for invalid in ({"ids": [comment["id"]], "action": "unknown"}, {"ids": [], "action": "approve"}):
            self.assertEqual(client.post("/api/admin/comments/batch", headers=editor, json=invalid).status_code, 422)
        self.assertEqual(client.get(path).json()["data"]["total"], 0)
        self.assertEqual(client.post("/api/admin/comments/batch", headers=editor, json={"ids": [str(comment["id"])], "action": "approve"}).status_code, 200)
        self.assertEqual(client.get(path).json()["data"]["total"], 1)
        self.assertEqual(client.put(status_path, headers=editor, json={"status": 0}).status_code, 200)
        self.assertEqual(client.get(path).json()["data"]["total"], 0)
        all_comments = client.get("/api/admin/comments?status=-1", headers=editor).json()["data"]["list"]
        self.assertIn(comment["id"], [row["id"] for row in all_comments])

    def test_public_statistics_exclude_unlisted_articles(self):
        from app.db.session import SessionLocal
        from app.models import Article, Category
        with SessionLocal() as db:
            category = Category(name="Public statistics")
            db.add(category)
            db.flush()
            db.add_all([
                Article(title="Visible stats", content="body", author_id=1, category_id=category.id, status=1, view_count=3),
                Article(title="Draft stats", content="body", author_id=1, category_id=category.id, status=0, view_count=5),
                Article(title="Private stats", content="body", author_id=1, category_id=category.id, status=1, password="secret", view_count=7),
                Article(title="Future stats", content="body", author_id=1, category_id=category.id, status=1, publish_at=datetime.utcnow() + timedelta(days=1), view_count=11),
            ])
            db.commit()
            category_id = category.id
        listing = self.client.get(f"/api/articles?categoryId={category_id}").json()["data"]
        categories = self.client.get("/api/categories").json()["data"]
        self.assertEqual(listing["total"], 1)
        self.assertEqual(next(c for c in categories if c["id"] == category_id)["articleCount"], listing["total"])
        site = self.client.get("/api/site").json()["data"]["stats"]
        public = self.client.get("/api/articles?pageSize=50").json()["data"]
        self.assertEqual(site["articleCount"], public["total"])
        self.assertEqual(site["viewCount"], sum(a["viewCount"] for a in public["list"]))

    def test_rag_only_uses_public_article_chunks(self):
        from app.db.session import SessionLocal
        from app.models import Article, ArticleChunk
        with SessionLocal() as db:
            articles = [
                Article(title="Visible", content="public excerpt", author_id=1, category_id=1, status=1),
                Article(title="Private", content="private excerpt", author_id=1, category_id=1, status=1, password="hidden"),
                Article(title="Future", content="future excerpt", author_id=1, category_id=1, status=1, publish_at=datetime.utcnow() + timedelta(days=1)),
                Article(title="Deleted", content="deleted excerpt", author_id=1, category_id=1, status=1, deleted_at=datetime.utcnow()),
            ]
            db.add_all(articles)
            db.flush()
            for article in articles:
                db.add(ArticleChunk(article_id=article.id, chunk_index=0, content=article.content, embedding=json.dumps([1.0, 0.0]).encode(), embedding_dim=2))
            db.add(ArticleChunk(article_id=articles[0].id, chunk_index=1, content="wrong model excerpt", embedding=json.dumps([1.0, 0.0]).encode(), embedding_dim=2, embedding_model="different-model"))
            db.commit()

        async def events():
            yield 'data: {"delta":"answer"}\n\n'
            yield 'data: [DONE]\n\n'

        with patch("app.api.v1.ai.embed", new=AsyncMock(return_value=[[1.0, 0.0]])), \
             patch("app.api.v1.ai.open_chat_stream", new=AsyncMock(return_value=events())) as stream:
            response = self.client.post("/api/ai/ask", json={"question": "What is public?"})
        self.assertEqual(response.status_code, 200)
        context = stream.await_args.args[0][0]["content"]
        self.assertIn("public excerpt", context)
        for hidden in ("private excerpt", "future excerpt", "deleted excerpt", "wrong model excerpt"):
            self.assertNotIn(hidden, context)

    def test_rag_failed_embedding_keeps_previous_index(self):
        from sqlalchemy import select
        from app.core.config import settings
        from app.db.session import SessionLocal
        from app.models import Article, ArticleChunk
        from app.rag.indexing import index_article
        with SessionLocal() as db:
            article = Article(title="Indexed article", content="new text", author_id=1, category_id=1, status=1)
            db.add(article)
            db.flush()
            db.add(ArticleChunk(article_id=article.id, chunk_index=0, content="old text", embedding=b"[1, 0]", embedding_dim=2))
            db.commit()
            with patch("app.rag.indexing.embed", new=AsyncMock(side_effect=RuntimeError("provider failed"))):
                with self.assertRaises(RuntimeError):
                    asyncio.run(index_article(db, article))
            self.assertEqual(db.scalar(select(ArticleChunk).where(ArticleChunk.article_id == article.id)).content, "old text")
            with patch("app.rag.indexing.embed", new=AsyncMock(return_value=[[1.0, 0.0]])):
                self.assertEqual(asyncio.run(index_article(db, article)), 1)
            chunk = db.scalar(select(ArticleChunk).where(ArticleChunk.article_id == article.id))
            self.assertEqual(chunk.content, "new text")
            self.assertEqual(chunk.embedding_model, settings.embedding_model)

    def test_ai_provider_summary_index_and_streaming(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        from sqlalchemy import select
        from app.core.config import settings
        from app.db.session import SessionLocal
        from app.models import Article, ArticleChunk
        from app.rag.indexing import index_article

        requests = []

        class Provider(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((self.path, payload, self.headers.get("Authorization")))
                if self.path.endswith("/embeddings"):
                    data = {"data": [{"embedding": [1.0, 0.0]} for _ in payload["input"]]}
                    body = json.dumps(data).encode()
                    content_type = "application/json"
                elif payload.get("stream"):
                    body = b'data: {"choices":[{"delta":{"content":"answer"}}]}\n\ndata: [DONE]\n\n'
                    content_type = "text/event-stream"
                else:
                    body = json.dumps({"choices": [{"message": {"content": "mock summary"}}]}).encode()
                    content_type = "application/json"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/v1"
            with patch.object(settings, "chat_base_url", url), patch.object(settings, "chat_api_key", "test-key"), \
                 patch.object(settings, "embedding_base_url", url), patch.object(settings, "embedding_api_key", "test-key"), \
                 patch.object(settings, "embedding_model", "mock-model"), patch.object(settings, "embedding_dimension", 2):
                admin = self.client.post("/api/admin/login", json={"username": "admin", "password": "123456"}).json()["data"]
                headers = {"Authorization": "Bearer " + admin["accessToken"]}
                response = self.client.post("/api/ai/summary", headers=headers, json={"content": "article content"})
                self.assertEqual(response.json()["data"]["summary"], "mock summary")
                with SessionLocal() as db:
                    article = Article(title="AI test", content="searchable excerpt", author_id=1, category_id=1, status=1)
                    db.add(article)
                    db.commit()
                    article_id = article.id
                    self.assertEqual(asyncio.run(index_article(db, article)), 1)
                response = self.client.post("/api/ai/ask", json={"question": "question", "articleId": article_id})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn('"delta": "answer"', response.text)
                self.assertIn("data: [DONE]", response.text)
                sent_chat = [payload for path, payload, _ in requests if path.endswith("/chat/completions") and payload.get("stream")][-1]
                self.assertIn("searchable excerpt", sent_chat["messages"][0]["content"])
                self.assertTrue(all(auth == "Bearer test-key" for _, _, auth in requests))
                with SessionLocal() as db:
                    article = db.get(Article, article_id)
                    article.content = ""
                    db.commit()
                    self.assertEqual(asyncio.run(index_article(db, article)), 0)
                    self.assertIsNone(db.scalar(select(ArticleChunk).where(ArticleChunk.article_id == article_id)))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_seed_restores_missing_setting_without_overwriting_existing_values(self):
        from app.db.seed import seed
        from app.db.session import SessionLocal
        from app.models import SiteSetting
        with SessionLocal() as db:
            title = db.get(SiteSetting, "site_title")
            title.value = "Existing title"
            db.delete(db.get(SiteSetting, "daily_quotes"))
            db.commit()
            seed(db)
            seed(db)
            self.assertEqual(db.get(SiteSetting, "site_title").value, "Existing title")
            self.assertTrue(db.get(SiteSetting, "daily_quotes").value)

    def test_legacy_go_bcrypt_login_upgrades_hash(self):
        from pwdlib.hashers.bcrypt import BcryptHasher
        from app.db.session import SessionLocal
        from app.models import User
        with SessionLocal() as db:
            user = User(username="legacy", email="legacy@example.test", password_hash=BcryptHasher(rounds=4, prefix="2a").hash("legacy-password"))
            db.add(user)
            db.commit()
            user_id = user.id
        response = self.client.post("/api/auth/login", json={"username": "legacy", "password": "legacy-password"})
        self.assertEqual(response.status_code, 200, response.text)
        with SessionLocal() as db:
            self.assertTrue(db.get(User, user_id).password_hash.startswith("$argon2"))

    def test_upload_validates_image_and_serves_generated_path(self):
        from PIL import Image
        client = self.client
        user = client.post("/api/auth/register", json={"username": "uploader", "email": "uploader@example.test", "password": "password123"}).json()["data"]
        headers = {"Authorization": "Bearer " + user["accessToken"]}
        invalid = client.post("/api/upload", headers=headers, files={"file": ("fake.png", b"\x89PNG\r\n\x1a\ninvalid", "image/png")})
        self.assertEqual(invalid.status_code, 415)
        buffer = BytesIO()
        Image.new("RGB", (2, 2), "white").save(buffer, format="PNG")
        uploaded = client.post("/api/upload", headers=headers, files={"file": ("../../image.png", buffer.getvalue(), "image/png")})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        image = uploaded.json()["data"]
        self.assertEqual(client.get(image["url"]).status_code, 200)
        self.assertNotIn("..", image["url"])
        admin = client.post("/api/admin/login", json={"username": "admin", "password": "123456"}).json()["data"]
        admin_headers = {"Authorization": "Bearer " + admin["accessToken"]}
        self.assertEqual(client.delete(f"/api/admin/images/{image['id']}", headers=admin_headers).status_code, 200)
        self.assertEqual(client.get(image["url"]).status_code, 404)

    def test_imported_nested_image_uses_stored_url_for_deletion(self):
        from app.core.config import settings
        from app.db.session import SessionLocal
        from app.models import Image
        path = Path(settings.upload_dir) / "2026" / "11" / "stored.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test image")
        with SessionLocal() as db:
            image = Image(name="original.png", url="/uploads/2026/11/stored.png", size=10, ext="png", uploader_id=1)
            db.add(image)
            db.commit()
            image_id = image.id
        admin = self.client.post("/api/admin/login", json={"username": "admin", "password": "123456"}).json()["data"]
        headers = {"Authorization": "Bearer " + admin["accessToken"]}
        self.assertEqual(self.client.delete(f"/api/admin/images/{image_id}", headers=headers).status_code, 200)
        self.assertFalse(path.exists())
        outside = Path(settings.upload_dir).parent / "outside.txt"
        outside.write_text("keep", encoding="utf-8")
        with SessionLocal() as db:
            image = Image(name="outside.txt", url="/uploads/%2e%2e/outside.txt", size=4, ext="png", uploader_id=1)
            db.add(image)
            db.commit()
            image_id = image.id
        self.assertEqual(self.client.delete(f"/api/admin/images/{image_id}", headers=headers).status_code, 400)
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    @unittest.skipUnless(Path("dist/index.html").is_file(), "frontend has not been built")
    def test_frontend_build_and_static_assets_are_served(self):
        for path in ("/", "/article.html", "/admin/dashboard.html", "/js/api.js", "/css/style.css"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
