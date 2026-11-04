"""Optional live tests; run only against a disposable Compose environment."""
import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import httpx
from PIL import Image


@unittest.skipUnless(os.environ.get("BLOG_SMOKE_BASE_URL"), "isolated Compose URL not configured")
class ComposeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_url = os.environ["BLOG_SMOKE_BASE_URL"]
        cls.client = httpx.Client(base_url=cls.base_url, timeout=30)
        response = cls.client.post("/api/admin/login", json={
            "username": os.environ["BLOG_SMOKE_ADMIN_USER"],
            "password": os.environ["BLOG_SMOKE_ADMIN_PASSWORD"],
        })
        response.raise_for_status()
        cls.admin = {"Authorization": "Bearer " + response.json()["data"]["accessToken"]}

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def request_ok(self, method, path, **kwargs):
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response

    def test_mysql_redis_and_static_workflow(self):
        for path in ("/health", "/ready", "/", "/js/api.js", "/admin/dashboard.html"):
            with self.subTest(path=path):
                self.assertEqual(self.request_ok("GET", path).status_code, 200)

        suffix = uuid.uuid4().hex[:12]
        author = self.request_ok("POST", "/api/auth/register", json={"username": "smoke_" + suffix, "email": suffix + "@validation.test", "password": "validation-pass-123"}).json()["data"]
        headers = {"Authorization": "Bearer " + author["accessToken"]}
        self.assertEqual(self.client.get("/api/admin/articles", headers=headers).status_code, 403)

        def refresh_once(_):
            return httpx.post(self.base_url + "/api/auth/refresh", json={"refreshToken": author["refreshToken"]}, timeout=30).status_code
        with ThreadPoolExecutor(max_workers=4) as pool:
            codes = list(pool.map(refresh_once, range(4)))
        self.assertEqual(sorted(codes), [200, 401, 401, 401])

        categories = self.request_ok("GET", "/api/categories").json()["data"]
        self.assertTrue(categories)
        content = "中" * 100000
        article = self.request_ok("POST", "/api/my/articles", headers=headers, json={"title": "Compose smoke " + suffix, "content": content, "categoryId": categories[0]["id"]}).json()["data"]
        article_id = article["id"]
        self.assertEqual(self.client.get(f"/api/articles/{article_id}").status_code, 404)
        self.request_ok("PUT", f"/api/admin/articles/{article_id}/approve", headers=self.admin)
        self.assertEqual(self.request_ok("GET", f"/api/articles/{article_id}").json()["data"]["content"], content)
        first_like = self.request_ok("PUT", f"/api/articles/{article_id}/like").json()["data"]
        self.assertEqual(first_like, {"likeCount": 1, "already": False})
        self.assertEqual(self.request_ok("PUT", f"/api/articles/{article_id}/like").json()["data"], {"likeCount": 0, "already": True})

        def toggle_like(use_auth):
            response = httpx.put(self.base_url + f"/api/articles/{article_id}/like", headers=headers if use_auth else {}, timeout=30)
            response.raise_for_status()
            return response.json()["data"]

        for use_auth in (False, True):
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(toggle_like, [use_auth] * 8))
            self.assertEqual(sum(result["already"] for result in results), 4)
            self.assertEqual(self.request_ok("GET", f"/api/articles/{article_id}").json()["data"]["likeCount"], 0)
        for _ in range(2):
            self.assertEqual(self.request_ok("PUT", f"/api/articles/{article_id}/view").json()["data"]["viewCount"], 1)

        comment = self.request_ok("POST", f"/api/articles/{article_id}/comments", json={"content": "hello", "nickname": "Visitor"}).json()["data"]
        self.assertEqual(self.request_ok("GET", f"/api/articles/{article_id}/comments").json()["data"]["total"], 0)
        self.request_ok("PUT", f"/api/admin/comments/{comment['id']}/approve", headers=self.admin)
        self.assertEqual(self.request_ok("GET", f"/api/articles/{article_id}/comments").json()["data"]["total"], 1)
        self.assertIn(f"/article.html?id={article_id}", self.request_ok("GET", "/api/rss.xml").text)

        self.request_ok("GET", "/api/categories")
        self.assertEqual(self.request_ok("GET", "/api/categories").headers.get("X-Cache"), "HIT")
        category_name = "cache_" + suffix
        self.request_ok("POST", "/api/admin/categories", headers=self.admin, json={"name": category_name})
        self.assertIn(category_name, [item["name"] for item in self.request_ok("GET", "/api/categories").json()["data"]])

        image_data = BytesIO()
        Image.new("RGB", (2, 2), "white").save(image_data, format="PNG")
        image = self.request_ok("POST", "/api/upload", headers=headers, files={"file": ("image.png", image_data.getvalue(), "image/png")}).json()["data"]
        self.request_ok("GET", image["url"])
        self.request_ok("DELETE", f"/api/admin/images/{image['id']}", headers=self.admin)
        self.assertEqual(self.client.get(image["url"]).status_code, 404)
        self.request_ok("DELETE", f"/api/my/articles/{article_id}", headers=headers)
        self.assertEqual(self.client.get(f"/api/articles/{article_id}").status_code, 404)
