"""Integration checks against a migrated, isolated SQLite database."""
import os
import json
import asyncio
from io import BytesIO
import tempfile
import unittest
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
        comment_id = comment.json()["data"]["id"]
        self.assertEqual(client.get(f"/api/articles/{article_id}/comments").json()["data"]["total"], 0)
        self.assertEqual(client.put(f"/api/admin/comments/{comment_id}/approve", headers=admin_headers).status_code, 200)
        self.assertEqual(client.get(f"/api/articles/{article_id}/comments").json()["data"]["total"], 1)
        expected_link = f"/article.html?id={article_id}"
        self.assertIn(expected_link, client.get("/api/rss.xml").text)
        self.assertIn(expected_link, client.get("/sitemap.xml").text)
        self.assertEqual(client.put("/api/admin/settings", headers=admin_headers, json={"quotes": [{"content": "Test quote", "author": "Author"}]}).status_code, 200)
        self.assertEqual(client.get("/api/quote/random").json()["data"], {"content": "Test quote", "author": "Author"})

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
