# Blog System Python

基于 Go Blog System 行为和既有 React/Vite 前端重写的 Python CMS。后端使用 FastAPI、SQLAlchemy 2、Alembic 和 MySQL 8；Redis 用于缓存/限流等可选运行能力；AI Chat 与 Embedding 使用独立的 OpenAI 兼容配置。

## 目录

```text
app/
├── api/v1/       # HTTP 路由、参数校验和响应映射
├── core/         # 配置、安全与统一错误
├── db/           # Session、种子数据、Alembic
├── integrations/ # AI 服务端客户端
├── models/       # SQLAlchemy 持久化模型
├── rag/          # 清洗、切块、索引和检索
├── repositories/ # 数据查询
├── schemas/      # Pydantic 输入/输出模型
└── services/     # 用例逻辑
web/              # 保留的 React/Vite 前端
docs/             # 需求规格、架构和迁移说明
```

## 本地开发

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
alembic upgrade head
python -m app.db.seed
uvicorn app.main:app --reload --port 8080
```

初始 schema 已由 Alembic revision `0001_initial` 管理。默认数据库为本地 SQLite；生产环境使用 MySQL 8。种子账号由 `BLOG_SEED_USERNAME`、`BLOG_SEED_EMAIL` 和 `BLOG_SEED_PASSWORD` 配置。生产环境启动会拒绝默认 JWT 密钥及默认种子账号设置。迁移脚本应先审查并提交，再用于部署；不要直接对既有 Go 数据库执行自动 schema 变更。

运行隔离数据库上的核心接口检查：

```powershell
python -m unittest discover -s tests/integration -v
```

前端使用 Node.js 20.19+ 或 22.12+；静态资源检查需要先在 `web/` 执行 `npm ci` 和 `npm run build`。

浏览器端到端测试仅应连接隔离测试站点：它会创建测试账号、文章和评论，结束时会软删除文章，但测试账号及相关记录仍会保留。先安装 `requirements-dev.txt` 和 Playwright Chromium，再设置隔离站点地址和管理员凭据：

```powershell
pip install -r requirements-dev.txt
python -m playwright install chromium
$env:BLOG_BROWSER_BASE_URL = 'http://127.0.0.1:18081'
$env:BLOG_BROWSER_ADMIN_USER = '<隔离站点管理员用户名>'
$env:BLOG_BROWSER_ADMIN_PASSWORD = '<隔离站点管理员密码>'
python -m unittest discover -s tests/e2e -v
```

如需使用本机已有的 Chromium 或 Edge，可通过 `BLOG_BROWSER_EXECUTABLE` 指定浏览器可执行文件的完整路径；否则使用 Playwright 安装的 Chromium。未设置上述三个环境变量时测试会跳过。

## Docker Compose

复制 `.env.example` 为 `.env`，替换所有凭据，然后运行：

```powershell
docker compose up --build
```

容器启动时执行已提交的 Alembic 迁移、幂等种子数据，再启动 API。Compose 构建前端静态资源并由 FastAPI 托管；上传文件保存在持久化卷。

## API 约定

- API 保留 `/api` 路径、小驼峰字段和 `{code,message,data}` JSON 信封。
- 认证失败返回 401，角色不足返回 403；未实现的 `/api/*` 返回 JSON 404。
- RSS、Sitemap、Markdown 导出和 SSE 使用各自内容类型。
- 核心路径包含公开文章、注册/登录/refresh、个人投稿、编辑审核、评论、分类/标签、用户管理、设置、友链、图库、审计与 AI/RAG。
- 健康检查为 `/health`，依赖就绪检查为 `/ready`。

需求验收与迁移差异见 `docs/需求规格说明.md` 和 `docs/目标架构与目录设计.md`。

## 导入 Go 版数据

先备份 Go 数据库和 `uploads/` 文件，停掉向目标库写入的服务。目标库必须是独立、已执行 `alembic upgrade head` 且尚未执行种子初始化的空库。通过环境变量设置 `BLOG_GO_DATABASE_URL` 和 `BLOG_DATABASE_URL`，然后在项目根目录执行：

```powershell
python -m scripts.import_go          # 只检查并输出数量，不写入目标库
python -m scripts.import_go --apply  # 将检查通过的数据作为单个事务导入
```

导入保留主键和关联；Go 用户 bcrypt 密码在登录成功后升级为 Argon2，私密文章的旧口令在导入时转换为 Argon2。旧浏览记录如果同一文章和访客出现多次，会按当前去重规则合并，脚本会在 `skipped` 中报告数量；文章的原有总浏览数保留。非公开文章的旧向量块不会导入。图片数据库记录不会复制图片文件，需要将 Go 项目的 `uploads/` 内容复制到 Python 项目的上传目录。

Go 版允许部分软删除名称重复，而 Python 当前库要求用户名、邮箱、分类名及标签名全局唯一；若存在重复，脚本会中止并要求先处理。实际切换前应在备份副本上核对记录数量、图片和登录流程。

文章正文在公开展示和私密文章解锁时会进行 HTML 安全过滤，包括旧数据；常用段落、标题、代码、表格和图片会保留，脚本、事件属性、自定义样式等不安全内容会被移除。编辑界面仍保留原文，迁移后建议检查旧文章的显示效果。
