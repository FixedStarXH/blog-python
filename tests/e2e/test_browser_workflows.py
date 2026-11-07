"""Opt-in browser test for a disposable, isolated Compose environment."""
import os
import unittest
import uuid


@unittest.skipUnless(
    all(os.environ.get(key) for key in ("BLOG_BROWSER_BASE_URL", "BLOG_BROWSER_ADMIN_USER", "BLOG_BROWSER_ADMIN_PASSWORD")),
    "disposable browser test environment not configured",
)
class BrowserWorkflowTests(unittest.TestCase):
    def test_admin_site_operations_pages(self):
        from playwright.sync_api import sync_playwright, expect

        base = os.environ["BLOG_BROWSER_BASE_URL"].rstrip("/")
        suffix = uuid.uuid4().hex[:8]
        link_name = "Ops " + suffix
        with sync_playwright() as playwright:
            executable = os.environ.get("BLOG_BROWSER_EXECUTABLE")
            browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.on("dialog", lambda dialog: dialog.accept())
            page.goto(base + "/admin/login.html")
            page.locator("#username").fill(os.environ["BLOG_BROWSER_ADMIN_USER"])
            page.locator("#password").fill(os.environ["BLOG_BROWSER_ADMIN_PASSWORD"])
            page.locator("button[type=submit]").click()
            page.wait_for_url("**/admin/dashboard.html")
            expect(page.locator("#stats")).to_contain_text("用户总数")

            page.goto(base + "/admin/settings.html")
            page.locator("#s-site_title").fill("Browser Ops " + suffix)
            page.get_by_role("button", name="保存全部设置").click()
            expect(page.get_by_text("设置已保存")).to_be_visible()
            public_site = page.request.get(base + "/api/site").json()["data"]
            self.assertEqual(public_site["title"], "Browser Ops " + suffix)

            page.goto(base + "/admin/links.html")
            page.get_by_role("button", name="+ 添加链接").click()
            page.locator("#l-name").fill(link_name)
            page.locator("#l-url").fill("https://example.test/" + suffix)
            page.locator("#l-desc").fill("Temporary browser check")
            page.locator("#form-modal button[type=submit]").click()
            row = page.locator("#tbody tr").filter(has_text=link_name)
            expect(row).to_be_visible()
            public_links = page.request.get(base + "/api/links").json()["data"]
            self.assertTrue(any(item["name"] == link_name for item in public_links))
            row.get_by_role("button", name="编辑").click()
            page.locator("#l-enabled").uncheck()
            page.locator("#form-modal button[type=submit]").click()
            expect(row).to_contain_text("停用")
            public_links = page.request.get(base + "/api/links").json()["data"]
            self.assertFalse(any(item["name"] == link_name for item in public_links))
            row.get_by_role("button", name="删除").click()
            expect(page.locator("#tbody")).not_to_contain_text(link_name)
            browser.close()

    def test_admin_image_gallery_upload_and_delete(self):
        from io import BytesIO
        from PIL import Image as PILImage
        from playwright.sync_api import sync_playwright, expect

        base = os.environ["BLOG_BROWSER_BASE_URL"].rstrip("/")
        buffer = BytesIO()
        PILImage.new("RGB", (3, 3), "orange").save(buffer, format="PNG")
        filename = "Gallery " + uuid.uuid4().hex[:8] + ".png"
        with sync_playwright() as playwright:
            executable = os.environ.get("BLOG_BROWSER_EXECUTABLE")
            browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
            page = browser.new_page()
            page.on("dialog", lambda dialog: dialog.accept())
            page.goto(base + "/admin/login.html")
            page.locator("#username").fill(os.environ["BLOG_BROWSER_ADMIN_USER"])
            page.locator("#password").fill(os.environ["BLOG_BROWSER_ADMIN_PASSWORD"])
            page.locator("button[type=submit]").click()
            page.wait_for_url("**/admin/dashboard.html")
            page.goto(base + "/admin/images.html")
            page.locator("#file-input").set_input_files({"name": filename, "mimeType": "image/png", "buffer": buffer.getvalue()})
            card = page.locator(".image-cell").filter(has_text=filename)
            expect(card).to_be_visible()
            expect(card.locator(".image-name")).to_have_text(filename)
            image_url = card.locator("img").get_attribute("src")
            self.assertTrue(image_url.startswith("/uploads/"))
            self.assertEqual(page.request.get(base + image_url).status, 200)
            expect(card.get_by_role("button", name="复制链接")).to_be_visible()
            card.get_by_role("button", name="删除 " + filename).click()
            expect(card).to_have_count(0)
            self.assertEqual(page.request.get(base + image_url).status, 404)
            browser.close()

    def test_admin_user_management_page(self):
        from playwright.sync_api import sync_playwright, expect

        base = os.environ["BLOG_BROWSER_BASE_URL"].rstrip("/")
        suffix = uuid.uuid4().hex[:10]
        username = "people" + suffix
        with sync_playwright() as playwright:
            executable = os.environ.get("BLOG_BROWSER_EXECUTABLE")
            browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.on("dialog", lambda dialog: dialog.accept())
            created = page.request.post(base + "/api/auth/register", data={
                "username": username, "email": username + "@validation.test", "password": "password123"
            })
            self.assertEqual(created.status, 201)
            tokens = created.json()["data"]
            page.goto(base + "/admin/login.html")
            page.locator("#username").fill(os.environ["BLOG_BROWSER_ADMIN_USER"])
            page.locator("#password").fill(os.environ["BLOG_BROWSER_ADMIN_PASSWORD"])
            page.locator("button[type=submit]").click()
            page.wait_for_url("**/admin/dashboard.html")
            page.goto(base + "/admin/users.html")
            expect(page.get_by_role("heading", name="用户管理")).to_be_visible()
            page.locator("#keyword").fill(username)
            page.get_by_role("button", name="搜索").click()
            row = page.locator("#tbody tr").filter(has_text=username)
            expect(row).to_be_visible()
            expect(page.locator("#total")).to_contain_text("1")
            row.locator("select[data-role]").select_option("2")
            expect(row.locator("select[data-role]")).to_have_value("2")
            page.locator("#role-filter").select_option("2")
            expect(row).to_be_visible()
            page.locator("#role-filter").select_option("")
            row.get_by_role("button", name="禁用").click()
            expect(row).to_contain_text("已禁用")
            self.assertEqual(page.request.post(base + "/api/auth/refresh", data={"refreshToken": tokens["refreshToken"]}).status, 401)
            row.get_by_role("button", name="启用").click()
            expect(row).to_contain_text("正常")
            self.assertEqual(page.request.post(base + "/api/auth/refresh", data={"refreshToken": tokens["refreshToken"]}).status, 401)
            row.locator("select[data-role]").select_option("1")
            expect(row.locator("select[data-role]")).to_have_value("1")
            browser.close()

    def test_tag_filter_loads_all_pages(self):
        from urllib.parse import parse_qs, urlsplit
        from playwright.sync_api import sync_playwright, expect

        with sync_playwright() as p:
            executable = os.environ.get("BLOG_BROWSER_EXECUTABLE")
            browser = p.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
            page = browser.new_page()
            page.route("**/api/tags", lambda route: route.fulfill(json={
                "code": 200, "message": "success", "data": [{"id": 987, "name": "Test tag", "articleCount": 51}]
            }))
            def articles(route):
                query = parse_qs(urlsplit(route.request.url).query)
                if query.get("tag") != ["Test tag"]:
                    return route.continue_()
                number = int(query.get("page", ["1"])[0])
                ids = range(1, 51) if number == 1 else [51]
                items = [{"id": i, "title": f"Tag article {i}", "createdAt": "2024-01-01T00:00:00",
                          "category": {"name": "Category"}, "viewCount": 0} for i in ids]
                route.fulfill(json={"code": 200, "message": "success",
                                    "data": {"list": items, "total": 51, "page": number, "pageSize": 50}})
            page.route("**/api/articles?*", articles)
            page.goto(os.environ["BLOG_BROWSER_BASE_URL"].rstrip("/") + "/tags.html")
            page.locator('.tag-clickable').filter(has_text='Test tag').click()
            expect(page.locator('#tag-result-total')).to_have_text('51')
            expect(page.get_by_text('Tag article 51', exact=True)).to_be_visible()
            browser.close()

    def test_register_submit_moderate_and_like(self):
        from playwright.sync_api import sync_playwright, expect

        BASE = os.environ["BLOG_BROWSER_BASE_URL"].rstrip("/")
        suffix = uuid.uuid4().hex[:10]
        username = "ui" + suffix
        title = "Browser test " + suffix
        comment = "Browser comment " + suffix
        with sync_playwright() as p:
            executable = os.environ.get("BLOG_BROWSER_EXECUTABLE")
            browser = p.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
            user = browser.new_context()
            page = user.new_page()
            page.goto(BASE + '/register.html')
            page.locator('#username').fill(username)
            page.locator('#email').fill(username + '@validation.test')
            page.locator('#password').fill('test-password-123')
            page.locator('#password2').fill('test-password-123')
            page.locator('form').first.locator('button[type=submit]').click()
            page.wait_for_url('**/me.html')
            page.locator('#column-name').fill('Browser column ' + suffix)
            page.locator('#column-description').fill('A test column')
            page.locator('#column-save').click()
            column_row = page.locator('.column-row').filter(has_text='Browser column ' + suffix)
            expect(column_row).to_be_visible()
            column_row.get_by_role('button', name='编辑').click()
            page.locator('#column-description').fill('Updated description')
            page.locator('#column-save').click()
            expect(column_row).to_contain_text('Updated description')
            page.goto(BASE + '/write.html')
            page.locator('#f-title').fill(title)
            page.wait_for_function("document.querySelectorAll('#f-category option').length > 1")
            page.locator('#f-category').select_option(index=1)
            page.locator('#f-column').select_option(label='Browser column ' + suffix)
            page.locator('#f-content').fill('Browser test body')
            page.locator('#submit-btn').click()
            page.wait_for_url('**/me.html')
            expect(page.get_by_text(title, exact=True)).to_be_visible()
            linked = page.evaluate("""async title => {
                const token = localStorage.getItem('accessToken');
                const response = await fetch('/api/my/articles', {headers: {Authorization: 'Bearer ' + token}});
                return (await response.json()).data.list.find(article => article.title === title);
            }""", title)
            self.assertIsNotNone(linked['columnId'])
            admin = browser.new_context()
            ap = admin.new_page()
            ap.on('dialog', lambda dialog: dialog.accept())
            ap.goto(BASE + '/admin/login.html')
            ap.locator('#username').fill(os.environ['BLOG_BROWSER_ADMIN_USER'])
            ap.locator('#password').fill(os.environ['BLOG_BROWSER_ADMIN_PASSWORD'])
            ap.locator('button[type=submit]').click()
            ap.wait_for_url('**/admin/dashboard.html')
            ap.goto(BASE + '/admin/articles.html')
            ap.locator('#f-keyword').fill(title)
            ap.get_by_role('button', name='筛选').click()
            row = ap.locator('#tbody tr').filter(has_text=title)
            expect(row).to_be_visible()
            article_id = row.locator('.row-check').get_attribute('data-id')
            row.locator('.row-check').check()
            ap.get_by_role('button', name='批量发布', exact=True).click()
            expect(row).to_contain_text('已发布')
            page.goto(BASE + '/article.html?id=' + article_id)
            expect(page.locator('#article-content')).to_contain_text('Browser test body')
            page.locator('#c-content').fill(comment)
            page.locator('#comment-form button[type=submit]').click()
            expect(page.locator('#c-content')).to_have_value('')
            expect(page.locator('#comment-list')).not_to_contain_text(comment)
            ap.goto(BASE + '/admin/comments.html')
            row = ap.locator('#tbody tr').filter(has_text=comment)
            expect(row).to_be_visible()
            row.get_by_role('button', name='通过').click()
            expect(row).to_have_count(0)
            page.reload()
            expect(page.locator('#comment-list')).to_contain_text(comment)
            link = page.locator('.like-link')
            link.click()
            expect(page.locator('#like-num')).to_have_text('1')
            link.click()
            expect(page.locator('#like-num')).to_have_text('0')
            def mock_comment_pages(route):
                from urllib.parse import parse_qs, urlsplit
                requested_page = parse_qs(urlsplit(route.request.url).query).get("page", ["1"])[0]
                if requested_page == "1":
                    items = [{"id": i, "nickname": "Reader", "content": f"Page comment {i}",
                              "parentId": None, "likeCount": 0, "createdAt": "2024-01-01T00:00:00"}
                             for i in range(1, 51)]
                else:
                    items = [{"id": 51, "nickname": "Reply", "content": "Visible orphan reply [unsafe](javascript:alert(1))",
                              "parentId": 9999, "likeCount": 0, "createdAt": "2024-01-01T00:00:00"}]
                route.fulfill(json={"code": 200, "message": "success",
                                    "data": {"list": items, "total": 51, "page": int(requested_page), "pageSize": 50}})

            page.route(f"**/api/articles/{article_id}/comments?*", mock_comment_pages)
            page.reload()
            expect(page.locator('#load-more-comments')).to_be_visible()
            expect(page.get_by_text('Visible orphan reply')).to_have_count(0)
            page.locator('#load-more-comments').click()
            expect(page.get_by_text('Visible orphan reply', exact=False)).to_be_visible()
            expect(page.locator('#comment-list a[href^="javascript:"]')).to_have_count(0)
            expect(page.locator('#load-more-comments')).to_have_count(0)
            safe_markup = page.evaluate("""() => {
                const previous = window.marked;
                window.marked = {parse: () => '<a href="javascript:alert(1)">unsafe</a><a href="/ok">safe</a>'};
                const result = renderCommentText('example');
                window.marked = previous;
                return result;
            }""")
            self.assertNotIn("javascript:", safe_markup)
            self.assertIn('href="/ok"', safe_markup)
            ap.goto(BASE + '/admin/articles.html')
            ap.get_by_role('button', name='+ 新建文章').click()
            ap.wait_for_function("document.querySelectorAll('#a-categoryId option').length > 0")
            ap.locator('#a-title').fill('Private browser test ' + suffix)
            ap.locator('#a-content').fill('Private browser content')
            ap.locator('#a-password').fill('private-test-pass')
            with ap.expect_response(lambda response: response.url.endswith('/api/admin/articles') and response.request.method == 'POST') as created:
                ap.locator('#form-modal button[type=submit]').click()
            self.assertEqual(created.value.status, 201)
            private_id = str(created.value.json()['data']['id'])
            page.goto(BASE + '/article.html?id=' + private_id)
            expect(page.locator('#lock-input')).to_be_visible()
            page.locator('#lock-input').fill('private-test-pass')
            page.get_by_role('button', name='解锁阅读').click()
            expect(page.locator('#article-content')).to_contain_text('Private browser content')
            admin_token = ap.evaluate("localStorage.getItem('accessToken')")
            removed_private = ap.request.delete(BASE + '/api/admin/articles/' + private_id, headers={"Authorization": "Bearer " + admin_token})
            self.assertEqual(removed_private.status, 200)

            token = page.evaluate("localStorage.getItem('accessToken')")
            deleted = page.request.delete(BASE + '/api/my/articles/' + article_id, headers={"Authorization": "Bearer " + token})
            self.assertEqual(deleted.status, 200)
            admin.close()
            user.close()
            browser.close()


if __name__ == "__main__":
    unittest.main()
