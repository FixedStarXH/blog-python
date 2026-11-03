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
