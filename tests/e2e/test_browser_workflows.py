"""Opt-in browser test for a disposable, isolated Compose environment."""
import os
import unittest
import uuid


@unittest.skipUnless(
    all(os.environ.get(key) for key in ("BLOG_BROWSER_BASE_URL", "BLOG_BROWSER_ADMIN_USER", "BLOG_BROWSER_ADMIN_PASSWORD")),
    "disposable browser test environment not configured",
)
class BrowserWorkflowTests(unittest.TestCase):
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
            page.goto(BASE + '/write.html')
            page.locator('#f-title').fill(title)
            page.wait_for_function("document.querySelectorAll('#f-category option').length > 1")
            page.locator('#f-category').select_option(index=1)
            page.locator('#f-content').fill('Browser test body')
            page.locator('#submit-btn').click()
            page.wait_for_url('**/me.html')
            expect(page.get_by_text(title, exact=True)).to_be_visible()
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
