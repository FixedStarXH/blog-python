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

    def test_security_headers_request_id_and_request_log_redaction(self):
        with self.assertLogs("blog", level="INFO") as captured:
            response = self.client.get(
                "/health?token=do-not-log",
                headers={"X-Request-ID": "invalid request id"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.headers["X-Request-ID"], "invalid request id")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["Referrer-Policy"], "strict-origin-when-cross-origin")
        self.assertNotIn("do-not-log", "\n".join(captured.output))

    def test_cors_allows_only_configured_origin(self):
        allowed = self.client.options(
            "/api/auth/login",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertEqual(allowed.headers.get("Access-Control-Allow-Origin"), "http://localhost:5173")
        denied = self.client.options(
            "/api/auth/login",
            headers={
                "Origin": "https://attacker.example",
                "Access-Control-Request-Method": "POST",
            },
        )
        self.assertNotEqual(denied.headers.get("Access-Control-Allow-Origin"), "https://attacker.example")

    def test_user_management_permissions_and_token_revocation(self):
        import uuid

        suffix = uuid.uuid4().hex[:10]
        admin_login = self.client.post("/api/admin/login", json={"username": "admin", "password": "123456"})
        self.assertEqual(admin_login.status_code, 200, admin_login.text)
        admin_data = admin_login.json()["data"]
        admin = {"Authorization": "Bearer " + admin_data["accessToken"]}
        owner = self.client.post("/api/auth/register", json={
            "username": "owner_" + suffix, "email": "owner_" + suffix + "@example.test", "password": "password123"
        }).json()["data"]
        peer = self.client.post("/api/auth/register", json={
            "username": "peer_" + suffix, "email": "peer_" + suffix + "@example.test", "password": "password123"
        }).json()["data"]
        owner_id = owner["user"]["id"]
        peer_id = peer["user"]["id"]
        owner_headers = {"Authorization": "Bearer " + owner["accessToken"]}
        peer_headers = {"Authorization": "Bearer " + peer["accessToken"]}

        path = f"/api/admin/users/{peer_id}"
        self.assertEqual(self.client.get("/api/admin/users").status_code, 401)
        self.assertEqual(self.client.get("/api/admin/users", headers=owner_headers).status_code, 403)
        self.assertEqual(self.client.put(path + "/role", headers=owner_headers, json={"role": 3}).status_code, 403)
        for body in ({}, {"role": "3"}, {"role": 4}, {"role": None}):
            self.assertEqual(self.client.put(path + "/role", headers=admin, json=body).status_code, 422)
        for body in ({}, {"status": "0"}, {"status": 2}, {"status": None}):
            self.assertEqual(self.client.put(path + "/status", headers=admin, json=body).status_code, 422)
        self.assertEqual(self.client.put(f"/api/admin/users/{admin_data['user']['id']}/status", headers=admin, json={"status": 0}).status_code, 403)
        self.assertEqual(self.client.put(f"/api/admin/users/{admin_data['user']['id']}/role", headers=admin, json={"role": 2}).status_code, 403)
        self.assertEqual(self.client.put("/api/admin/users/999999/role", headers=admin, json={"role": 2}).status_code, 404)
        self.assertEqual(self.client.get("/api/admin/users?role=0", headers=admin).status_code, 422)
        self.assertEqual(self.client.get("/api/admin/users?pageSize=101", headers=admin).status_code, 422)

        self.assertEqual(self.client.put(path + "/role", headers=admin, json={"role": 2}).status_code, 200)
        filtered = self.client.get("/api/admin/users", headers=admin, params={"keyword": "peer_" + suffix, "role": 2}).json()["data"]
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["list"][0]["id"], peer_id)
        self.assertEqual(self.client.get("/api/admin/articles", headers=peer_headers).status_code, 200)
        self.assertEqual(self.client.get("/api/admin/users", headers=peer_headers).status_code, 403)
        self.assertEqual(self.client.put(path + "/role", headers=admin, json={"role": 1}).status_code, 200)
        self.assertEqual(self.client.get("/api/admin/articles", headers=peer_headers).status_code, 403)
        self.assertEqual(self.client.post("/api/admin/login", json={"username": "peer_" + suffix, "password": "password123"}).status_code, 403)

        self.assertEqual(self.client.put(f"/api/admin/users/{owner_id}/status", headers=admin, json={"status": 0}).status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me", headers=owner_headers).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/refresh", json={"refreshToken": owner["refreshToken"]}).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/login", json={"username": "owner_" + suffix, "password": "password123"}).status_code, 401)
        self.assertEqual(self.client.put(f"/api/admin/users/{owner_id}/status", headers=admin, json={"status": 1}).status_code, 200)
        self.assertEqual(self.client.post("/api/auth/refresh", json={"refreshToken": owner["refreshToken"]}).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/login", json={"username": "owner_" + suffix, "password": "password123"}).status_code, 200)

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

    def test_submission_review_scheduling_and_trash(self):
        from app.db.session import SessionLocal
        from app.models import Article, Category
        client = self.client
        author = client.post("/api/auth/register", json={
            "username": "review_author", "email": "review_author@example.test", "password": "password123"
        }).json()["data"]
        headers = {"Authorization": "Bearer " + author["accessToken"]}
        admin = client.post("/api/admin/login", json={"username": "admin", "password": "123456"}).json()["data"]
        editor = {"Authorization": "Bearer " + admin["accessToken"]}
        payload = {"title": "Review workflow", "content": "body", "categoryId": 1}
        article_id = client.post("/api/my/articles", headers=headers, json=payload).json()["data"]["id"]
        self.assertEqual(client.put(f"/api/admin/articles/{article_id}/approve", headers=headers).status_code, 403)
        for reason in ("", "   "):
            self.assertEqual(client.put(f"/api/admin/articles/{article_id}/reject", headers=editor, json={"reason": reason}).status_code, 422)
        rejected = client.put(f"/api/admin/articles/{article_id}/reject", headers=editor, json={"reason": "Please revise"})
        self.assertEqual((rejected.json()["data"]["status"], rejected.json()["data"]["rejectReason"]), (3, "Please revise"))
        resubmitted = client.put(f"/api/my/articles/{article_id}", headers=headers, json=payload)
        self.assertEqual(resubmitted.json()["data"]["status"], 2)
        future = (datetime.utcnow() + timedelta(days=1)).isoformat()
        scheduled = client.put(f"/api/my/articles/{article_id}", headers=headers, json={**payload, "publishAt": future})
        self.assertEqual(scheduled.status_code, 200)
        self.assertEqual(client.put(f"/api/admin/articles/{article_id}/approve", headers=editor).json()["data"]["status"], 4)
        self.assertEqual(client.get(f"/api/articles/{article_id}").status_code, 404)
        unchanged = client.put(f"/api/my/articles/{article_id}", headers=headers, json=payload).json()["data"]
        self.assertEqual(unchanged["status"], 4)
        self.assertIsNotNone(unchanged["publishAt"])
        unscheduled = client.put(f"/api/my/articles/{article_id}", headers=headers, json={**payload, "publishAt": None}).json()["data"]
        self.assertEqual((unscheduled["status"], unscheduled["publishAt"]), (2, None))
        self.assertEqual(client.put(f"/api/admin/articles/{article_id}/approve", headers=editor).json()["data"]["status"], 1)
        self.assertEqual(client.delete(f"/api/my/articles/{article_id}", headers=headers).status_code, 200)
        self.assertEqual(client.get(f"/api/articles/{article_id}").status_code, 404)
        self.assertIn(article_id, [row["id"] for row in client.get("/api/admin/articles/trash", headers=editor).json()["data"]["list"]])
        self.assertEqual(client.put(f"/api/admin/articles/{article_id}/restore", headers=editor).status_code, 200)
        self.assertEqual(client.get(f"/api/articles/{article_id}").status_code, 200)
        with SessionLocal() as db:
            category = Category(name="Deleted admin category", deleted_at=datetime.utcnow())
            db.add(category)
            db.commit()
            category_id = category.id
        self.assertEqual(client.post("/api/admin/articles", headers=editor, json={**payload, "categoryId": category_id}).status_code, 400)
        invalid_update = {**payload, "title": "Should not be saved", "categoryId": category_id}
        self.assertEqual(client.put(f"/api/admin/articles/{article_id}", headers=editor, json=invalid_update).status_code, 400)
        self.assertEqual(client.get(f"/api/admin/articles/{article_id}", headers=editor).json()["data"]["title"], payload["title"])
        self.assertEqual(client.post("/api/admin/articles", headers=editor, json={**payload, "status": 4}).status_code, 400)
        for invalid in ({"ids": [], "action": "publish"}, {"ids": [article_id], "action": "unknown"}, {"ids": ["bad"], "action": "draft"}):
            self.assertEqual(client.post("/api/admin/articles/batch", headers=editor, json=invalid).status_code, 422)
        self.assertEqual(client.get(f"/api/articles/{article_id}").status_code, 200)
        batch_path = "/api/admin/articles/batch"
        for action, field, expected in (
            ("top", "isTop", True), ("untop", "isTop", False),
            ("draft", "status", 0), ("publish", "status", 1),
        ):
            result = client.post(batch_path, headers=editor, json={"ids": [str(article_id)], "action": action})
            self.assertEqual(result.json()["data"]["affected"], 1)
            detail = client.get(f"/api/admin/articles/{article_id}", headers=editor).json()["data"]
            self.assertEqual(detail[field], expected)
        result = client.post(batch_path, headers=editor, json={"ids": [article_id], "action": "delete"})
        self.assertEqual(result.json()["data"]["affected"], 1)
        self.assertEqual(client.get(f"/api/articles/{article_id}").status_code, 404)

    def test_concurrent_review_and_scheduled_publication(self):
        from sqlalchemy import select
        from app.db.session import SessionLocal
        from app.models import Article
        from app.tasks.scheduler import publish_due_articles, publishing_loop
        admin = self.client.post("/api/admin/login", json={"username": "admin", "password": "123456"}).json()["data"]
        editor = {"Authorization": "Bearer " + admin["accessToken"]}
        created = self.client.post("/api/admin/articles", headers=editor, json={
            "title": "Concurrent review", "content": "body", "categoryId": 1, "status": 2
        }).json()["data"]
        article_id = created["id"]
        def review(action):
            return self.client.put(f"/api/admin/articles/{article_id}/{action}", headers=editor,
                                   json={"reason": "Revise"} if action == "reject" else None)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(review, ["approve", "reject"]))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        with SessionLocal() as db:
            now = datetime.utcnow()
            due = Article(title="Due", content="body", author_id=1, category_id=1, status=4, publish_at=now-timedelta(seconds=1))
            future = Article(title="Later", content="body", author_id=1, category_id=1, status=4, publish_at=now+timedelta(days=1))
            deleted = Article(title="Deleted scheduled", content="body", author_id=1, category_id=1, status=4, publish_at=now-timedelta(seconds=1), deleted_at=now)
            db.add_all([due, future, deleted])
            db.commit()
            due_id, future_id, deleted_id = due.id, future.id, deleted.id
            self.assertEqual(publish_due_articles(db, now), [due_id])
            self.assertEqual(publish_due_articles(db, now), [])
            self.assertEqual(db.scalar(select(Article.status).where(Article.id == future_id)), 4)
            self.assertEqual(db.scalar(select(Article.status).where(Article.id == deleted_id)), 4)
        with SessionLocal() as db:
            db.get(Article, due_id).status = 4
            db.commit()
        async def run_once():
            with patch("app.tasks.scheduler.asyncio.sleep", new=AsyncMock(side_effect=[None, asyncio.CancelledError])), \
                 patch("app.tasks.scheduler.settings.embedding_base_url", ""):
                self.app.state.redis = AsyncMock()
                try:
                    with self.assertRaises(asyncio.CancelledError):
                        await publishing_loop(self.app)
                    self.app.state.redis.incr.assert_awaited_once_with("blog:http:version")
                finally:
                    self.app.state.redis = None
        asyncio.run(run_once())

    def test_concurrent_schedulers_publish_each_article_once(self):
        from app.db.session import SessionLocal
        from app.models import Article
        from app.tasks.scheduler import publish_due_articles
        now = datetime.utcnow()
        with SessionLocal() as db:
            articles = [Article(title=f"Scheduled race {i}", content="body", author_id=1,
                                category_id=1, status=4, publish_at=now-timedelta(seconds=1))
                        for i in range(10)]
            db.add_all(articles)
            db.commit()
            expected = {article.id for article in articles}
        def publish(_):
            with SessionLocal() as db:
                return publish_due_articles(db, now)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(publish, range(2)))
        self.assertEqual(set(results[0]) | set(results[1]), expected)
        self.assertTrue(set(results[0]).isdisjoint(results[1]))

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
            first = self.client.get(path,headers={"X-Request-ID":"trace-cache-miss"})
            self.assertEqual(first.headers["X-Cache"], "MISS")
            self.assertEqual(first.headers["X-Request-ID"],"trace-cache-miss")
            self.assertNotIn("onerror", first.json()["data"]["content"])
            with self.assertLogs("blog",level="INFO") as captured:
                second = self.client.get(path,headers={"X-Request-ID":"trace-cache-hit"})
            self.assertEqual(second.headers["X-Cache"], "HIT")
            self.assertEqual(second.headers["X-Request-ID"],"trace-cache-hit")
            log_line=next(line for line in captured.output if '"event":"http_request"' in line)
            cache_log=json.loads(log_line[log_line.index("{"):])
            self.assertEqual((cache_log["request_id"],cache_log["cache"]),("trace-cache-hit","HIT"))
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
        self.assertEqual(client.get("/api/quote").json()["data"], "Test quote | Author")
        self.assertEqual(client.get("/api/quote/random").json()["data"], {"content": "Test quote", "author": "Author"})

    def test_operations_settings_links_dashboard_and_feeds_contracts(self):
        import xml.etree.ElementTree as ET
        from app.db.session import SessionLocal
        from app.models import SiteSetting

        admin_data=self.client.post("/api/admin/login",json={"username":"admin","password":"123456"}).json()["data"]
        headers={"Authorization":"Bearer "+admin_data["accessToken"]}
        with SessionLocal() as db:
            db.add_all([SiteSetting(key="internal_feature_flag",value="do-not-publish"),SiteSetting(key="provider_secret",value="private")])
            db.commit()

        public=self.client.get("/api/settings").json()["data"]
        self.assertNotIn("internal_feature_flag",public)
        self.assertNotIn("provider_secret",public)
        admin_settings=self.client.get("/api/admin/settings",headers=headers).json()["data"]
        self.assertNotIn("internal_feature_flag",admin_settings)
        self.assertNotIn("provider_secret",admin_settings)
        for body in ({"unknown_option":"x"},{"site_title":123},{"site_title":"x"*256},{"quotes":[{"content":""}]}):
            self.assertEqual(self.client.put("/api/admin/settings",headers=headers,json=body).status_code,422)
        saved=self.client.put("/api/admin/settings",headers=headers,json={"site_title":"Ops & Blog","quotes":[{"content":"A < B","author":"Writer"}]})
        self.assertEqual(saved.status_code,200,saved.text)
        self.assertEqual(self.client.get("/api/quote/random").json()["data"],{"content":"A < B","author":"Writer"})
        self.assertEqual(self.client.get("/api/site").json()["data"]["title"],"Ops & Blog")

        valid={"name":"Docs","url":"https://example.test/docs","description":"Documentation","sort":2,"enabled":False}
        for invalid in ({**valid,"enabled":"false"},{**valid,"sort":"2"},{**valid,"url":"javascript:alert(1)"},{**valid,"name":" "},{**valid,"description":"x"*201}):
            self.assertEqual(self.client.post("/api/admin/links",headers=headers,json=invalid).status_code,422)
        created=self.client.post("/api/admin/links",headers=headers,json={**valid,"enabled":True})
        self.assertEqual(created.status_code,201,created.text)
        link_id=created.json()["data"]["id"]
        self.assertTrue(any(link["id"]==link_id for link in self.client.get("/api/links").json()["data"]))
        updated=self.client.put(f"/api/admin/links/{link_id}",headers=headers,json={**valid,"sort":-1})
        self.assertEqual(updated.status_code,200,updated.text)
        self.assertFalse(any(link["id"]==link_id for link in self.client.get("/api/links").json()["data"]))
        self.assertEqual(self.client.delete(f"/api/admin/links/{link_id}",headers=headers).status_code,200)
        self.assertEqual(self.client.put(f"/api/admin/links/{link_id}",headers=headers,json=valid).status_code,404)

        dashboard=self.client.get("/api/admin/dashboard",headers=headers).json()["data"]
        self.assertIn("users",dashboard["counts"])
        self.assertGreaterEqual(dashboard["counts"]["users"],1)
        trend=self.client.get("/api/admin/dashboard/views-trend?days=7",headers=headers).json()["data"]
        self.assertEqual(len(trend),7)
        self.assertEqual([point["date"] for point in trend],sorted(point["date"] for point in trend))

        rss=ET.fromstring(self.client.get("/api/rss.xml").content)
        sitemap=ET.fromstring(self.client.get("/sitemap.xml").content)
        self.assertEqual(rss.tag,"rss")
        self.assertEqual(rss.findtext("channel/title"),"Ops & Blog")
        self.assertIsNotNone(sitemap.find("{http://www.sitemaps.org/schemas/sitemap/0.9}url"))

    def test_audit_records_results_filters_and_clear_semantics(self):
        from sqlalchemy import select
        from app.db.session import SessionLocal
        from app.models import AuditLog

        login=self.client.post("/api/admin/login",json={"username":"admin","password":"123456"}).json()["data"]
        admin={"Authorization":"Bearer "+login["accessToken"]}
        with self.assertLogs("blog",level="INFO") as captured:
            failed_login=self.client.post("/api/admin/login",json={"username":"missing-user","password":"never-log-this-secret"},headers={"X-Request-ID":"audit-trace-1"})
        self.assertEqual(failed_login.status_code,401)
        self.assertEqual(failed_login.headers["X-Request-ID"],"audit-trace-1")
        log_line=next(line for line in captured.output if '"event":"http_request"' in line)
        log_record=json.loads(log_line[log_line.index("{"):])
        self.assertEqual(log_record["request_id"],"audit-trace-1")
        self.assertNotIn("never-log-this-secret","\n".join(captured.output))

        with SessionLocal() as db:
            successful_login=db.scalar(select(AuditLog).where(AuditLog.action=="POST /api/admin/login",AuditLog.status==1).order_by(AuditLog.id.desc()))
            self.assertEqual((successful_login.operator_id,successful_login.role,successful_login.target_type,successful_login.target_id),(login["user"]["id"],3,"account",login["user"]["id"]))
            failed=db.scalar(select(AuditLog).where(AuditLog.action=="POST /api/admin/login").order_by(AuditLog.id.desc()))
            self.assertEqual((failed.operator_id,failed.role,failed.target_type,failed.status),(0,0,"account",0))
            self.assertEqual(failed.target_id,0)
            self.assertIn("HTTP 401",failed.detail)

        invalid=self.client.post("/api/admin/links",headers=admin,json={"name":"bad","url":"javascript:alert(1)"})
        self.assertEqual(invalid.status_code,422)
        created=self.client.post("/api/admin/links",headers=admin,json={"name":"Audited","url":"https://example.test/audit","enabled":True})
        self.assertEqual(created.status_code,201,created.text)
        link_id=created.json()["data"]["id"]
        self.assertEqual(self.client.delete("/api/admin/links/987654321",headers=admin).status_code,404)
        with SessionLocal() as db:
            success=db.scalar(select(AuditLog).where(AuditLog.action=="POST /api/admin/links").order_by(AuditLog.id.desc()))
            self.assertEqual((success.operator_id,success.username,success.role,success.target_type,success.target_id,success.status),(login["user"]["id"],"admin",3,"links",link_id,1))
            invalid_log=db.scalar(select(AuditLog).where(AuditLog.path=="/api/admin/links",AuditLog.status==0).order_by(AuditLog.id.desc()))
            self.assertEqual((invalid_log.operator_id,invalid_log.target_type,invalid_log.target_id),(login["user"]["id"],"links",0))

        filtered=self.client.get("/api/admin/audit-logs",headers=admin,params={"action":"POST /api/admin/links","status":1,"operatorId":login["user"]["id"],"page":1,"pageSize":5})
        self.assertEqual(filtered.status_code,200,filtered.text)
        self.assertTrue(filtered.json()["data"]["list"])
        self.assertTrue(all(row["targetType"]=="links" and row["roleName"]=="管理员" for row in filtered.json()["data"]["list"]))
        for params in ({"page":0},{"pageSize":101},{"status":2}):
            self.assertEqual(self.client.get("/api/admin/audit-logs",headers=admin,params=params).status_code,422)

        suffix=__import__("uuid").uuid4().hex[:8]
        def concurrent_create(index):
            return self.client.post("/api/admin/links",headers=admin,json={"name":f"Concurrent {suffix} {index}","url":f"https://example.test/{suffix}/{index}","enabled":True})
        with ThreadPoolExecutor(max_workers=4) as pool:
            concurrent=list(pool.map(concurrent_create,range(4)))
        self.assertTrue(all(response.status_code==201 for response in concurrent))
        concurrent_ids={response.json()["data"]["id"] for response in concurrent}
        with SessionLocal() as db:
            events=db.scalars(select(AuditLog).where(AuditLog.target_type=="links",AuditLog.target_id.in_(concurrent_ids))).all()
            self.assertEqual({event.target_id for event in events},concurrent_ids)
            self.assertTrue(all(event.status==1 for event in events))

        from fastapi.responses import JSONResponse
        from unittest.mock import AsyncMock, patch
        with patch("app.main.enforce_rate_limit",new=AsyncMock(return_value=JSONResponse(status_code=429,content={"code":429,"message":"limited","data":None},headers={"Retry-After":"60"}))):
            with self.assertLogs("blog",level="INFO") as captured:
                limited=self.client.post("/api/admin/links",headers={**admin,"X-Request-ID":"trace-limited-1"},json={"name":"Should not persist","url":"https://example.test/limited"})
        self.assertEqual(limited.status_code,429)
        self.assertEqual(limited.headers["X-Request-ID"],"trace-limited-1")
        self.assertEqual(limited.headers["Retry-After"],"60")
        log_line=next(line for line in captured.output if '"event":"http_request"' in line)
        limited_log=json.loads(log_line[log_line.index("{"):])
        self.assertEqual((limited_log["request_id"],limited_log["status_code"]),("trace-limited-1",429))

        registered=self.client.post("/api/auth/register",json={"username":"audit_viewer","email":"audit_viewer@example.test","password":"password123"}).json()["data"]
        viewer={"Authorization":"Bearer "+registered["accessToken"]}
        self.assertEqual(self.client.delete("/api/admin/audit-logs",headers=viewer).status_code,403)
        with SessionLocal() as db:
            denied=db.scalar(select(AuditLog).where(AuditLog.path=="/api/admin/audit-logs",AuditLog.status==0).order_by(AuditLog.id.desc()))
            self.assertEqual((denied.operator_id,denied.role,denied.status),(registered["user"]["id"],1,0))
        self.assertEqual(self.client.delete("/api/admin/audit-logs",headers=admin).status_code,200)
        with SessionLocal() as db:
            remaining=db.scalars(select(AuditLog)).all()
            self.assertEqual(len(remaining),1)
            self.assertEqual((remaining[0].action,remaining[0].status,remaining[0].operator_id),("DELETE /api/admin/audit-logs",1,login["user"]["id"]))

    def test_health_and_readiness_component_results(self):
        from unittest.mock import patch
        from app.main import engine

        self.assertEqual(self.client.get("/health").json(),{"status":"ok"})
        with patch("redis.Redis.from_url") as redis_client:
            redis_client.return_value.ping.return_value=True
            ready=self.client.get("/ready")
            self.assertEqual(ready.status_code,200)
            self.assertEqual(ready.json(),{"status":"ready","components":{"database":"ok","redis":"ok"}})
            with patch.object(engine,"connect",side_effect=RuntimeError("database unavailable")):
                unavailable=self.client.get("/ready")
            self.assertEqual(unavailable.status_code,503)
            self.assertEqual(unavailable.json()["components"],{"database":"unavailable","redis":"ok"})
            redis_client.return_value.ping.side_effect=RuntimeError("redis unavailable")
            unavailable=self.client.get("/ready")
            self.assertEqual(unavailable.status_code,503)
            self.assertEqual(unavailable.json()["components"],{"database":"ok","redis":"unavailable"})

    def test_audit_and_business_database_failures_are_reported_separately(self):
        from unittest.mock import patch
        from sqlalchemy import select
        from app.api.deps import get_db
        from app.db.session import SessionLocal
        from app.models import AuditLog, Link

        login=self.client.post("/api/admin/login",json={"username":"admin","password":"123456"}).json()["data"]
        headers={"Authorization":"Bearer "+login["accessToken"]}
        payload={"name":"Audit failure probe","url":"https://example.test/audit-failure","enabled":True}

        def failed_db_dependency():
            with SessionLocal() as db:
                def fail_commit(): raise RuntimeError("simulated database commit failure")
                db.commit=fail_commit
                yield db

        self.app.dependency_overrides[get_db]=failed_db_dependency
        try:
            rolled_back=self.client.post("/api/admin/links",headers={**headers,"X-Request-ID":"trace-db-failure"},json={**payload,"name":"Rollback probe"})
        finally:
            self.app.dependency_overrides.pop(get_db,None)
        self.assertEqual(rolled_back.status_code,500)
        self.assertEqual(rolled_back.headers["X-Request-ID"],"trace-db-failure")
        with SessionLocal() as db:
            self.assertIsNone(db.scalar(select(Link).where(Link.name=="Rollback probe")))
            rollback_event=db.scalar(select(AuditLog).where(AuditLog.path=="/api/admin/links").order_by(AuditLog.id.desc()))
            self.assertEqual((rollback_event.status,rollback_event.target_type), (0,"links"))

        with patch("app.main.SessionLocal",side_effect=RuntimeError("simulated audit store failure")):
            with self.assertLogs("blog",level="ERROR") as captured:
                persisted=self.client.post("/api/admin/links",headers=headers,json=payload)
        self.assertEqual(persisted.status_code,201,persisted.text)
        self.assertTrue(any("audit_write_failed" in line for line in captured.output))
        with SessionLocal() as db:
            self.assertIsNotNone(db.scalar(select(Link).where(Link.name=="Audit failure probe")))

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

    def test_comment_pagination_replies_and_concurrent_likes(self):
        from sqlalchemy import func, select
        from app.db.session import SessionLocal
        from app.models import Article, Comment, MetricEvent

        with SessionLocal() as db:
            article = Article(title="Comment pages", content="body", author_id=1, category_id=1, status=1)
            db.add(article)
            db.flush()
            rows = [Comment(article_id=article.id, nickname="Reader", content=f"Comment {i}",
                            status=1, created_at=datetime(2024, 1, 1)) for i in range(52)]
            db.add_all(rows)
            db.commit()
            article_id, first_id = article.id, rows[0].id

        path = f"/api/articles/{article_id}/comments"
        first = self.client.get(path + "?page=1&pageSize=50").json()["data"]
        second = self.client.get(path + "?page=2&pageSize=50").json()["data"]
        self.assertEqual(first["total"], 52)
        self.assertEqual(len(first["list"]), 50)
        self.assertEqual(len(second["list"]), 2)
        self.assertTrue({row["id"] for row in first["list"]}.isdisjoint(row["id"] for row in second["list"]))
        for query in ("?page=0", "?pageSize=0", "?pageSize=101"):
            self.assertEqual(self.client.get(path + query).status_code, 422)
        for body in ({"content": "   "}, {"content": "reply", "parentId": 0}):
            self.assertEqual(self.client.post(path, json=body).status_code, 422)
        reply = self.client.post(path, json={"content": "reply", "parentId": first_id})
        self.assertEqual(reply.status_code, 201)
        self.assertEqual(reply.json()["data"]["parentId"], first_id)
        self.assertEqual(reply.json()["data"]["status"], 0)
        self.assertEqual(self.client.post(path, json={"content": "bad parent", "parentId": reply.json()["data"]["id"]}).status_code, 404)
        self.assertEqual(self.client.put(f"/api/admin/comments/{first_id}/approve").status_code, 401)
        like_path = f"{path}/{first_id}/like"
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: self.client.put(like_path), range(6)))
        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertTrue(all(response.json()["data"]["likeCount"] == 1 for response in responses))
        with SessionLocal() as db:
            self.assertEqual(db.get(Comment, first_id).like_count, 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(MetricEvent).where(
                MetricEvent.article_id == article_id, MetricEvent.kind == "c_like"
            )), 1)

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

    def test_taxonomy_columns_and_recoverable_article_references(self):
        from sqlalchemy import func, select
        from app.db.session import SessionLocal
        from app.models import Article, article_tags
        client = self.client
        admin = client.post("/api/admin/login", json={"username": "admin", "password": "123456"}).json()["data"]
        editor = {"Authorization": "Bearer " + admin["accessToken"]}
        author = client.post("/api/auth/register", json={
            "username": "column_owner", "email": "column_owner@example.test", "password": "password123"
        }).json()["data"]
        other = client.post("/api/auth/register", json={
            "username": "column_other", "email": "column_other@example.test", "password": "password123"
        }).json()["data"]
        owner = {"Authorization": "Bearer " + author["accessToken"]}
        outsider = {"Authorization": "Bearer " + other["accessToken"]}
        self.assertEqual(client.post("/api/admin/categories", headers=owner, json={"name": "Forbidden"}).status_code, 403)
        for path, body in (("/api/admin/categories", {"name": "  "}), ("/api/admin/tags", {"name": "x" * 51}),
                           ("/api/my/columns", {"name": ""})):
            self.assertEqual(client.post(path, headers=owner if "columns" in path else editor, json=body).status_code, 422)
        category = client.post("/api/admin/categories", headers=editor, json={"name": " Test taxonomy ", "sort": -2}).json()["data"]
        category_id = category["id"]
        self.assertEqual(category["name"], "Test taxonomy")
        self.assertEqual(client.post("/api/admin/categories", headers=editor, json={"name": "Test taxonomy"}).status_code, 409)
        self.assertEqual(client.put(f"/api/admin/categories/{category_id}", headers=editor, json={"sort": 0}).status_code, 200)
        tag = client.post("/api/admin/tags", headers=editor, json={"name": "Test taxonomy tag"}).json()["data"]
        tag_id = tag["id"]
        self.assertEqual(client.post("/api/admin/tags", headers=editor, json={"name": "Test taxonomy tag"}).status_code, 409)
        self.assertEqual(client.get("/api/admin/tags?pageSize=0", headers=editor).status_code, 422)
        column = client.post("/api/my/columns", headers=owner, json={"name": "My column"}).json()["data"]
        column_id = column["id"]
        self.assertEqual(client.put(f"/api/my/columns/{column_id}", headers=outsider, json={"name": "Stolen"}).status_code, 404)
        self.assertEqual(client.delete(f"/api/my/columns/{column_id}", headers=outsider).status_code, 404)
        self.assertEqual(client.put(f"/api/my/columns/{column_id}", headers=owner, json={"status": 0}).status_code, 200)
        self.assertNotIn(column_id, [row["id"] for row in client.get("/api/columns").json()["data"]])
        self.assertEqual(client.put(f"/api/my/columns/{column_id}", headers=owner, json={"status": 1}).status_code, 200)
        payload = {"title": "Column post", "content": "body", "categoryId": category_id, "columnId": column_id, "tagIds": [tag_id]}
        self.assertEqual(client.post("/api/my/articles", headers=outsider, json=payload).status_code, 400)
        article = client.post("/api/my/articles", headers=owner, json=payload)
        self.assertEqual(article.status_code, 201, article.text)
        article_id = article.json()["data"]["id"]
        admin_category = next(row for row in client.get("/api/admin/categories", headers=editor).json()["data"] if row["id"] == category_id)
        self.assertEqual(admin_category["articleCount"], 1)
        self.assertEqual(client.put(f"/api/admin/articles/{article_id}/approve", headers=editor).status_code, 200)
        tag_public = next(row for row in client.get("/api/tags").json()["data"] if row["id"] == tag_id)
        self.assertEqual(tag_public["articleCount"], 1)
        self.assertEqual(client.get(f"/api/articles?tag={tag['name']}").json()["data"]["total"], 1)
        changed = client.put(f"/api/my/articles/{article_id}", headers=owner, json={
            "title": "Edited", "content": "body", "categoryId": category_id, "tagIds": [tag_id]
        })
        self.assertEqual(changed.json()["data"]["columnId"], column_id)
        admin_changed = client.put(f"/api/admin/articles/{article_id}", headers=editor, json={
            "title": "Admin edit", "content": "body", "categoryId": category_id, "tagIds": [tag_id]
        })
        self.assertEqual(admin_changed.json()["data"]["columnId"], column_id)
        self.assertEqual(client.delete(f"/api/admin/categories/{category_id}", headers=editor).status_code, 409)
        self.assertEqual(client.delete(f"/api/my/columns/{column_id}", headers=owner).status_code, 409)
        self.assertEqual(client.delete(f"/api/my/articles/{article_id}", headers=owner).status_code, 200)
        self.assertEqual(client.delete(f"/api/admin/categories/{category_id}", headers=editor).status_code, 409)
        self.assertEqual(client.delete(f"/api/my/columns/{column_id}", headers=owner).status_code, 409)
        self.assertEqual(client.delete(f"/api/admin/tags/{tag_id}", headers=editor).status_code, 200)
        with SessionLocal() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(article_tags).where(article_tags.c.tag_id == tag_id)), 0)
            self.assertEqual(db.get(Article, article_id).column_id, column_id)
        self.assertEqual(client.put(f"/api/admin/articles/{article_id}/restore", headers=editor).status_code, 200)
        self.assertEqual(client.get(f"/api/my/articles/{article_id}", headers=owner).json()["data"]["tags"], [])

    def test_concurrent_category_delete_and_submission_keep_references_valid(self):
        from sqlalchemy import select
        from app.db.session import SessionLocal
        from app.models import Article, Category
        admin = self.client.post("/api/admin/login", json={"username": "admin", "password": "123456"}).json()["data"]
        editor = {"Authorization": "Bearer " + admin["accessToken"]}
        author = self.client.post("/api/auth/register", json={
            "username": "taxonomy_race", "email": "taxonomy_race@example.test", "password": "password123"
        }).json()["data"]
        owner = {"Authorization": "Bearer " + author["accessToken"]}
        category = self.client.post("/api/admin/categories", headers=editor, json={"name": "Race category"}).json()["data"]["id"]
        barrier = threading.Barrier(2, timeout=10)
        def submit():
            barrier.wait()
            return self.client.post("/api/my/articles", headers=owner, json={
                "title": "Race article", "content": "body", "categoryId": category
            }).status_code
        def remove():
            barrier.wait()
            return self.client.delete(f"/api/admin/categories/{category}", headers=editor).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            submitted = pool.submit(submit)
            removed = pool.submit(remove)
            result = (submitted.result(), removed.result())
        self.assertIn(result, ((201, 409), (400, 200)))
        with SessionLocal() as db:
            item = db.get(Category, category)
            article = db.scalar(select(Article).where(Article.category_id == category))
            self.assertFalse(article is not None and item.deleted_at is not None)

    def test_public_statistics_exclude_unlisted_articles(self):
        from app.db.session import SessionLocal
        from app.models import Article, Category
        from app.services.view_counts import flush_pending_views
        self.enterContext(patch("app.tasks.scheduler.flush_pending_views", return_value=0))
        flush_pending_views()
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
        from app.core.config import settings
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

        with patch.object(settings,"chat_base_url","http://mock.test/v1"), patch.object(settings,"chat_api_key","test-key"), \
             patch.object(settings,"embedding_base_url","http://mock.test/v1"), patch.object(settings,"embedding_api_key","test-key"), \
             patch("app.services.ai_assistant.embed", new=AsyncMock(return_value=[[1.0, 0.0]])), \
             patch("app.services.ai_assistant.open_chat_stream", new=AsyncMock(return_value=events())) as stream:
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

    def test_persistent_index_job_coalesces_retries_and_swaps_atomically(self):
        from sqlalchemy import select
        from app.db.session import SessionLocal
        from app.models import Article, ArticleChunk, ArticleIndexJob
        from app.rag.jobs import enqueue_article, claim_due_jobs, process_index_job

        with SessionLocal() as db:
            article=Article(title="Queued index",content="old source",author_id=1,category_id=1,status=1)
            db.add(article); db.flush(); article_id=article.id
            db.add(ArticleChunk(article_id=article_id,chunk_index=0,content="old indexed source",embedding=b"[1.0,0.0]",embedding_dim=2))
            enqueue_article(db,article); db.commit()
            article.content="latest source text"; db.commit()
            enqueue_article(db,article); enqueue_article(db,article); db.commit()
            job=db.get(ArticleIndexJob,article_id)
            self.assertEqual((job.status,job.attempts),("pending",0))
            self.assertEqual(len(db.scalars(select(ArticleIndexJob).where(ArticleIndexJob.article_id==article_id)).all()),1)
            self.assertEqual(claim_due_jobs(db,article_id=article_id),[article_id])
            self.assertEqual(claim_due_jobs(db,article_id=article_id),[])

        with patch("app.rag.jobs.embed",new=AsyncMock(side_effect=RuntimeError("temporary provider failure"))):
            self.assertEqual(asyncio.run(process_index_job(article_id)),0)
        with SessionLocal() as db:
            job=db.get(ArticleIndexJob,article_id)
            self.assertEqual((job.status,job.attempts),("retry",1))
            self.assertTrue(job.last_error.startswith("worker:"))
            self.assertGreater(job.next_attempt_at,datetime.utcnow()+timedelta(seconds=25))
            self.assertEqual(db.scalar(select(ArticleChunk).where(ArticleChunk.article_id==article_id)).content,"old indexed source")
            job.next_attempt_at=datetime.utcnow()-timedelta(seconds=1); db.commit()
            self.assertEqual(claim_due_jobs(db,article_id=article_id),[article_id])

        with patch("app.rag.jobs.embed",new=AsyncMock(return_value=[[1.0,0.0]])):
            self.assertEqual(asyncio.run(process_index_job(article_id)),1)
        with SessionLocal() as db:
            job=db.get(ArticleIndexJob,article_id)
            chunk=db.scalar(select(ArticleChunk).where(ArticleChunk.article_id==article_id))
            self.assertEqual((job.status,job.attempts,job.chunk_count),("complete",2,1))
            self.assertEqual(chunk.content,"latest source text")
            db.delete(chunk); db.commit()
            enqueue_article(db,db.get(Article,article_id)); db.commit()
            self.assertEqual(db.get(ArticleIndexJob,article_id).status,"pending")

    def test_index_worker_discards_result_when_source_changes_mid_request(self):
        from sqlalchemy import select
        from app.db.session import SessionLocal
        from app.models import Article, ArticleChunk, ArticleIndexJob
        from app.rag.jobs import enqueue_article, claim_due_jobs, process_index_job

        with SessionLocal() as db:
            article=Article(title="Changing source",content="version one",author_id=1,category_id=1,status=1)
            db.add(article); db.flush(); article_id=article.id
            db.add(ArticleChunk(article_id=article_id,chunk_index=0,content="previous valid index",embedding=b"[1,0]",embedding_dim=2))
            enqueue_article(db,article); db.commit(); self.assertEqual(claim_due_jobs(db,article_id=article_id),[article_id])

        async def change_during_embed(_chunks):
            with SessionLocal() as db:
                db.get(Article,article_id).content="version two"; db.commit()
            return [[1.0,0.0]]

        with patch("app.rag.jobs.embed",new=AsyncMock(side_effect=change_during_embed)):
            self.assertEqual(asyncio.run(process_index_job(article_id)),0)
        with SessionLocal() as db:
            self.assertEqual(db.get(ArticleIndexJob,article_id).status,"pending")
            self.assertEqual(db.scalar(select(ArticleChunk).where(ArticleChunk.article_id==article_id)).content,"previous valid index")

    def test_explicit_embedding_unsupported_uses_public_fulltext_fallback(self):
        from app.db.session import SessionLocal
        from app.integrations.ai.client import EmbeddingUnsupported
        from app.models import Article, ArticleChunk
        from app.rag.indexing import index_article

        with SessionLocal() as db:
            article=Article(title="Fallback search",content="orchid retrieval fallback marker",author_id=1,category_id=1,status=1)
            db.add(article); db.commit(); article_id=article.id
            with patch("app.rag.indexing.embed",new=AsyncMock(side_effect=EmbeddingUnsupported())):
                self.assertEqual(asyncio.run(index_article(db,article)),1)
            chunk=db.query(ArticleChunk).filter_by(article_id=article_id).one()
            self.assertIsNone(chunk.embedding)
            self.assertEqual(chunk.embedding_model,"fulltext")

        async def events():
            yield 'data: [DONE]\n\n'
        from app.core.config import settings
        with patch.object(settings,"chat_base_url","http://mock.test/v1"), patch.object(settings,"chat_api_key","test-key"), \
             patch.object(settings,"embedding_base_url","http://mock.test/v1"), patch.object(settings,"embedding_api_key","test-key"), \
             patch("app.services.ai_assistant.embed",new=AsyncMock(side_effect=EmbeddingUnsupported())), \
             patch("app.services.ai_assistant.open_chat_stream",new=AsyncMock(return_value=events())) as stream:
            response=self.client.post("/api/ai/ask",json={"question":"orchid retrieval"})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn("orchid retrieval fallback marker",stream.await_args.args[0][0]["content"])

    def test_embedding_vectors_reject_nonfinite_values(self):
        from app.core.config import settings
        from app.core.errors import DomainError
        from app.integrations.ai.client import embed
        invalid_responses=[
            ({"data":[]},"invalid embedding response"),
            ({"data":[{"embedding":[1.0]}]},"dimension mismatch"),
            ({"data":[{"embedding":[1.0,float("nan")]}]},"finite numbers"),
        ]
        with patch.object(settings,"embedding_base_url","http://provider.test/v1"), \
             patch.object(settings,"embedding_api_key","test-key"), \
             patch.object(settings,"embedding_dimension",2):
            for payload,message in invalid_responses:
                with self.subTest(message=message), patch("app.integrations.ai.client.request_json",new=AsyncMock(return_value=payload)):
                    with self.assertRaisesRegex(DomainError,message):
                        asyncio.run(embed(["text"]))

    def test_embedding_fulltext_fallback_requires_explicit_provider_code(self):
        import httpx
        from app.core.config import settings
        from app.core.errors import DomainError
        from app.integrations.ai.client import EmbeddingUnsupported, embed
        original_client=httpx.AsyncClient

        async def verify(payload):
            transport=httpx.MockTransport(lambda request:httpx.Response(404,json=payload))
            with patch("app.integrations.ai.client.httpx.AsyncClient",
                       side_effect=lambda **kwargs:original_client(transport=transport,**kwargs)):
                return await embed(["probe"])

        with patch.object(settings,"embedding_base_url","http://provider.test/v1"), patch.object(settings,"embedding_api_key","test-key"):
            with self.assertRaises(EmbeddingUnsupported):
                asyncio.run(verify({"error":{"code":"embedding_not_supported"}}))
            with self.assertRaises(DomainError) as raised:
                asyncio.run(verify({"error":{"code":"model_not_found"}}))
            self.assertNotIsInstance(raised.exception,EmbeddingUnsupported)

    def test_embedding_job_stops_after_maximum_attempts(self):
        from app.db.session import SessionLocal
        from app.models import Article, ArticleIndexJob
        from app.rag.jobs import MAX_ATTEMPTS, enqueue_article, claim_due_jobs, process_index_job
        with SessionLocal() as db:
            article=Article(title="Exhaust index",content="retry forever? no",author_id=1,category_id=1,status=1)
            db.add(article); db.flush(); article_id=article.id
            enqueue_article(db,article); db.commit()
        with patch("app.rag.jobs.embed",new=AsyncMock(side_effect=RuntimeError("provider offline"))):
            for attempt in range(MAX_ATTEMPTS):
                with SessionLocal() as db:
                    job=db.get(ArticleIndexJob,article_id)
                    job.next_attempt_at=datetime.utcnow()-timedelta(seconds=1); db.commit()
                    self.assertEqual(claim_due_jobs(db,article_id=article_id),[article_id])
                self.assertEqual(asyncio.run(process_index_job(article_id)),0)
        with SessionLocal() as db:
            job=db.get(ArticleIndexJob,article_id)
            self.assertEqual((job.status,job.attempts),("failed",MAX_ATTEMPTS))
            self.assertTrue(job.last_error.startswith("worker:"))

    def test_index_job_expired_lease_is_reclaimed_after_worker_restart(self):
        from app.db.session import SessionLocal
        from app.models import Article, ArticleIndexJob
        from app.rag.jobs import enqueue_article, claim_due_jobs
        with SessionLocal() as db:
            article=Article(title="Worker restart",content="recover lease",author_id=1,category_id=1,status=1)
            db.add(article); db.flush(); article_id=article.id
            enqueue_article(db,article); db.commit()
            self.assertEqual(claim_due_jobs(db,article_id=article_id),[article_id])
            job=db.get(ArticleIndexJob,article_id); job.lease_until=datetime.utcnow()-timedelta(seconds=1); db.commit()
        with SessionLocal() as db:
            self.assertEqual(claim_due_jobs(db,article_id=article_id),[article_id])
            job=db.get(ArticleIndexJob,article_id)
            self.assertEqual((job.status,job.attempts),("processing",2))

    def test_index_job_classifies_authentication_failure_as_terminal(self):
        from app.core.errors import DomainError
        from app.db.session import SessionLocal
        from app.models import Article, ArticleIndexJob
        from app.rag.jobs import enqueue_article, claim_due_jobs, process_index_job
        with SessionLocal() as db:
            article=Article(title="Bad provider credentials",content="secret key not included",author_id=1,category_id=1,status=1)
            db.add(article); db.flush(); article_id=article.id
            enqueue_article(db,article); db.commit()
            self.assertEqual(claim_due_jobs(db,article_id=article_id),[article_id])
        with patch("app.rag.jobs.embed",new=AsyncMock(side_effect=DomainError("AI provider returned HTTP 401",502))):
            self.assertEqual(asyncio.run(process_index_job(article_id)),0)
        with SessionLocal() as db:
            job=db.get(ArticleIndexJob,article_id)
            self.assertEqual((job.status,job.attempts),("failed",1))
            self.assertTrue(job.last_error.startswith("authentication:"))
            self.assertNotIn("secret key",job.last_error)
        login=self.client.post("/api/admin/login",json={"username":"admin","password":"123456"}).json()["data"]
        status=self.client.get("/api/admin/ai/index-status",headers={"Authorization":"Bearer "+login["accessToken"]}).json()["data"]
        self.assertGreaterEqual(status["jobs"]["failed"],1)
        self.assertTrue(any(x["articleId"]==article_id and x["error"].startswith("authentication:") for x in status["recentFailures"]))

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

    def test_ai_daily_quota_counts_only_validated_calls_and_is_configurable(self):
        from app.core.config import settings

        class RedisCounter:
            def __init__(self): self.values={}; self.expirations=[]
            async def incr(self,key):
                self.values[key]=self.values.get(key,0)+1
                return self.values[key]
            async def expire(self,key,seconds): self.expirations.append((key,seconds))

        login=self.client.post("/api/auth/login",json={"username":"admin","password":"123456"}).json()["data"]
        headers={"Authorization":"Bearer "+login["accessToken"]}
        redis=RedisCounter(); previous_redis=self.app.state.redis
        self.app.state.redis=redis
        try:
            with patch.object(settings,"ai_daily_limit",2), \
                 patch.object(settings,"chat_base_url","http://mock.test/v1"), \
                 patch.object(settings,"chat_api_key","test-key"), \
                 patch("app.services.ai_assistant.complete",new=AsyncMock(return_value="摘要")):
                invalid=self.client.post("/api/ai/summary",headers=headers,json={"content":"  "})
                self.assertEqual(invalid.status_code,422)
                self.assertFalse(any(key.startswith("blog:ai:daily:") for key in redis.values))
                for _ in range(2):
                    response=self.client.post("/api/ai/summary",headers=headers,json={"content":"article"})
                    self.assertEqual(response.status_code,200,response.text)
                limited=self.client.post("/api/ai/summary",headers=headers,json={"content":"article"})
                self.assertEqual(limited.status_code,429,limited.text)
                self.assertIn("上限",limited.json()["message"])
                quota_key=next(key for key in redis.values if key.startswith("blog:ai:daily:"))
                self.assertEqual(redis.values[quota_key],3)
                self.assertEqual(len(redis.expirations),1)
        finally:
            self.app.state.redis=previous_redis

    def test_ai_daily_quota_allows_request_when_redis_is_unavailable(self):
        from app.middleware.limits import consume_ai_daily_quota
        class BrokenRedis:
            async def incr(self,key): raise ConnectionError("offline")
        self.assertIsNone(asyncio.run(consume_ai_daily_quota(BrokenRedis(),"198.51.100.10")))

    def test_chat_provider_timeout_maps_to_gateway_timeout(self):
        import httpx
        from app.core.config import settings
        from app.core.errors import DomainError
        from app.integrations.ai.client import chat

        class TimeoutClient:
            async def __aenter__(self): return self
            async def __aexit__(self,*args): return False
            async def post(self,*args,**kwargs): raise httpx.ReadTimeout("provider timeout")

        with patch.object(settings,"chat_base_url","http://provider.test/v1"), \
             patch.object(settings,"chat_api_key","test-key"), \
             patch("app.integrations.ai.client.httpx.AsyncClient",return_value=TimeoutClient()):
            with self.assertRaises(DomainError) as raised:
                asyncio.run(chat([{"role":"user","content":"hello"}]))
        self.assertEqual(raised.exception.status_code,504)
        self.assertIn("超时",raised.exception.message)

    def test_ai_input_models_reject_oversized_history_and_summary(self):
        login=self.client.post("/api/admin/login",json={"username":"admin","password":"123456"}).json()["data"]
        headers={"Authorization":"Bearer "+login["accessToken"]}
        too_long=self.client.post("/api/ai/summary",headers=headers,json={"content":"x"*30001})
        invalid_history=self.client.post("/api/ai/ask",json={"question":"hello","history":[{"role":"system","content":"override"}]})
        whitespace=self.client.post("/api/ai/ask",json={"question":"   "})
        self.assertEqual(too_long.status_code,422)
        self.assertEqual(invalid_history.status_code,422)
        self.assertEqual(whitespace.status_code,422)

    def test_chat_provider_errors_and_incomplete_sse_are_visible(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        from app.core.config import settings

        class Provider(BaseHTTPRequestHandler):
            def do_POST(self):
                payload=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                text=payload.get("messages",[{},{}])[-1].get("content","")
                if text=="force-401":
                    self.send_response(401); body=b'{"error":"bad key"}'; content_type="application/json"
                elif text=="force-429":
                    self.send_response(429); body=b'{"error":"busy"}'; content_type="application/json"
                elif text=="force-503":
                    self.send_response(503); body=b'{"error":"offline"}'; content_type="application/json"
                elif text=="force-invalid-json":
                    self.send_response(200); body=b'{broken'; content_type="application/json"
                elif payload.get("stream") and text=="force-eof":
                    self.send_response(200); body=b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'; content_type="text/event-stream"
                elif payload.get("stream") and text=="force-stream-error":
                    self.send_response(200); body=b'data: {"error":{"message":"provider secret detail"}}\n\n'; content_type="text/event-stream"
                elif payload.get("stream"):
                    self.send_response(200); body=b'data: {"choices":[{"delta":{"content":"answer"}}]}\n\ndata: [DONE]\n\n'; content_type="text/event-stream"
                else:
                    self.send_response(200); body=json.dumps({"choices":[{"message":{"content":"ok"}}]}).encode(); content_type="application/json"
                self.send_header("Content-Type",content_type); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
            def log_message(self,*args): pass

        server=ThreadingHTTPServer(("127.0.0.1",0),Provider)
        thread=Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            url=f"http://127.0.0.1:{server.server_port}/v1"
            with patch.object(settings,"chat_base_url",url),patch.object(settings,"chat_api_key","test-key"):
                admin=self.client.post("/api/admin/login",json={"username":"admin","password":"123456"}).json()["data"]
                headers={"Authorization":"Bearer "+admin["accessToken"]}
                for source,expected in (("force-401","认证失败"),("force-429","额度受限"),("force-503","暂时不可用"),("force-invalid-json","无效 JSON")):
                    response=self.client.post("/api/ai/summary",headers=headers,json={"content":source})
                    self.assertEqual(response.status_code,502 if source in {"force-401","force-invalid-json","force-503"} else 503,response.text)
                    self.assertIn(expected,response.json()["message"])
                for source,expected_error in (("force-eof","连接中断"),("force-stream-error","生成失败")):
                    response=self.client.post("/api/ai/polish",headers=headers,json={"content":source})
                    self.assertEqual(response.status_code,200,response.text)
                    self.assertIn('"error"',response.text)
                    self.assertIn(expected_error,response.text)
                    self.assertNotIn("data: [DONE]",response.text)
                    self.assertNotIn("provider secret detail",response.text)
        finally:
            server.shutdown(); server.server_close(); thread.join()

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

    def test_gallery_upload_pagination_and_validation(self):
        import uuid
        from PIL import Image as PILImage

        admin_data = self.client.post("/api/admin/login", json={"username": "admin", "password": "123456"}).json()["data"]
        headers = {"Authorization": "Bearer " + admin_data["accessToken"]}
        suffix = uuid.uuid4().hex[:8]
        reader = self.client.post("/api/auth/register", json={
            "username": "gallery_" + suffix, "email": "gallery_" + suffix + "@example.test", "password": "password123"
        }).json()["data"]
        reader_headers = {"Authorization": "Bearer " + reader["accessToken"]}
        buffer = BytesIO()
        PILImage.new("RGB", (3, 3), "blue").save(buffer, format="PNG")
        content = buffer.getvalue()
        path = "/api/admin/upload"
        self.assertEqual(self.client.post(path, files={"file": ("image.png", content, "image/png")}).status_code, 401)
        self.assertEqual(self.client.post(path, headers=reader_headers, files={"file": ("image.png", content, "image/png")}).status_code, 403)
        self.assertEqual(self.client.post(path, headers=headers, files={"file": ("bad.svg", content, "image/svg+xml")}).status_code, 415)
        self.assertEqual(self.client.post(path, headers=headers, files={"file": ("bad.jpg", content, "image/jpeg")}).status_code, 415)
        self.assertEqual(self.client.post(path, headers=headers, files={"file": ("large.png", b"x" * (5 * 1024 * 1024 + 1), "image/png")}).status_code, 413)
        self.assertEqual(self.client.post(path, headers=headers, files={"file": ("broken.png", b"\x89PNG\r\n\x1a\ninvalid", "image/png")}).status_code, 415)
        self.assertEqual(self.client.get("/api/admin/images?page=0", headers=headers).status_code, 422)
        self.assertEqual(self.client.get("/api/admin/images?pageSize=101", headers=headers).status_code, 422)

        first = self.client.post(path, headers=headers, files={"file": ("../../Photo.PNG", content, "image/png")})
        second = self.client.post(path, headers=headers, files={"file": ("Photo.PNG", content, "image/png")})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        images = [first.json()["data"], second.json()["data"]]
        self.assertEqual([item["name"] for item in images], ["Photo.PNG", "Photo.PNG"])
        self.assertNotEqual(images[0]["url"], images[1]["url"])
        for item in images:
            self.assertTrue(item["url"].startswith("/uploads/"))
            self.assertEqual(self.client.get(item["url"]).content, content)
        listing = self.client.get("/api/admin/images?pageSize=1", headers=headers).json()["data"]
        self.assertEqual(listing["list"][0]["id"], images[1]["id"])
        self.assertEqual(listing["list"][0]["name"], "Photo.PNG")
        self.assertEqual(listing["list"][0]["ext"], "png")
        self.assertEqual(listing["list"][0]["uploaderId"], admin_data["user"]["id"])
        self.assertGreaterEqual(listing["total"], 2)
        self.assertEqual(self.client.get("/api/admin/images?page=2&pageSize=1", headers=headers).json()["data"]["list"][0]["id"], images[0]["id"])
        for item in images:
            self.assertEqual(self.client.delete(f"/api/admin/images/{item['id']}", headers=headers).status_code, 200)
            self.assertEqual(self.client.get(item["url"]).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/admin/images/{images[0]['id']}", headers=headers).status_code, 404)
        for extension, image_format in (("jpg", "JPEG"), ("jpeg", "JPEG"), ("gif", "GIF"), ("webp", "WEBP")):
            with self.subTest(extension=extension):
                content_buffer = BytesIO()
                PILImage.new("RGB", (3, 3), "blue").save(content_buffer, format=image_format)
                filename = "valid." + extension
                uploaded = self.client.post(path, headers=headers, files={"file": (filename, content_buffer.getvalue(), "image/" + image_format.lower())})
                self.assertEqual(uploaded.status_code, 200, uploaded.text)
                item = uploaded.json()["data"]
                self.assertEqual(item["name"], filename)
                self.assertEqual(self.client.get(item["url"]).content, content_buffer.getvalue())
                self.assertEqual(self.client.delete(f"/api/admin/images/{item['id']}", headers=headers).status_code, 200)

    def test_image_database_failure_restores_file_state(self):
        from PIL import Image as PILImage
        from app.core.config import settings
        from app.core.errors import DomainError
        from app.db.session import SessionLocal
        from app.models import Image
        from app.services import images as image_service

        buffer = BytesIO()
        PILImage.new("RGB", (2, 2), "green").save(buffer, format="PNG")
        root = Path(settings.upload_dir)
        before = {path.name for path in root.iterdir() if path.is_file()}
        with SessionLocal() as db:
            with patch.object(db, "commit", side_effect=RuntimeError("test failure")), patch("app.services.images.logger.exception"):
                with self.assertRaises(DomainError) as error:
                    image_service.save_image(db, "rollback.png", buffer.getvalue(), 1)
            self.assertEqual(error.exception.status_code, 500)
        self.assertEqual({path.name for path in root.iterdir() if path.is_file()}, before)

        admin_data = self.client.post("/api/admin/login", json={"username": "admin", "password": "123456"}).json()["data"]
        headers = {"Authorization": "Bearer " + admin_data["accessToken"]}
        uploaded = self.client.post("/api/admin/upload", headers=headers,
                                    files={"file": ("rollback.png", buffer.getvalue(), "image/png")}).json()["data"]
        disk_file = root / uploaded["url"].rsplit("/", 1)[-1]
        with SessionLocal() as db:
            with patch.object(db, "commit", side_effect=RuntimeError("test failure")), patch("app.services.images.logger.exception"):
                with self.assertRaises(DomainError) as error:
                    image_service.delete_image(db, uploaded["id"])
            self.assertEqual(error.exception.status_code, 500)
            self.assertIsNotNone(db.get(Image, uploaded["id"]))
        self.assertTrue(disk_file.is_file())
        self.assertFalse(list(root.glob(".deleting-*")))
        self.assertEqual(self.client.delete(f"/api/admin/images/{uploaded['id']}", headers=headers).status_code, 200)

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
        with SessionLocal() as db:
            missing = Image(name="missing.png", url="/uploads/old/missing.png", size=1, ext="png", uploader_id=1)
            remote = Image(name="remote.png", url="https://example.test/uploads/remote.png", size=1, ext="png", uploader_id=1)
            db.add_all([missing, remote])
            db.commit()
            missing_id, remote_id = missing.id, remote.id
        self.assertEqual(self.client.delete(f"/api/admin/images/{missing_id}", headers=headers).status_code, 200)
        self.assertEqual(self.client.delete(f"/api/admin/images/{remote_id}", headers=headers).status_code, 400)

    @unittest.skipUnless(Path("dist/index.html").is_file(), "frontend has not been built")
    def test_frontend_build_and_static_assets_are_served(self):
        for path in ("/", "/article.html", "/admin/dashboard.html", "/js/api.js", "/css/style.css"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
