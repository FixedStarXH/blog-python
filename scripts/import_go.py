"""Import the Go blog schema into an empty, migrated Python database.

Set BLOG_GO_DATABASE_URL and BLOG_DATABASE_URL in the environment. The default
run only inspects and validates data; --apply performs one destination transaction.
"""
import argparse
import hashlib
import json
import math
import os
import struct
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from pwdlib.hashers.bcrypt import BcryptHasher
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, Table, create_engine, func, inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.security import hash_password
from app.db.base import Base
from app import models  # noqa: F401 - registers ORM tables


class ImportErrorWithContext(Exception):
    pass


REQUIRED = {
    "users": ("id", "username", "email", "password"),
    "categories": ("id", "name"),
    "articles": ("id", "title", "content", "author_id", "category_id"),
}
OPTIONAL_COLUMNS = {
    "tags": ("id", "name"), "columns": ("id", "name", "author_id"),
    "article_tags": ("article_id", "tag_id"), "comments": ("id", "content", "article_id"),
    "settings": ("k", "v"), "links": ("id", "name", "url"),
    "images": ("id", "name", "url", "size", "ext", "uploader_id"),
    "audit_logs": ("id", "action"), "article_views": ("article_id", "ip"),
    "article_likes": ("article_id", "ip"), "article_chunks": ("id", "article_id", "chunk_index", "content", "embedding"),
}
OPTIONAL = ("tags", "columns", "article_tags", "comments", "settings", "links",
            "images", "audit_logs", "article_views", "article_likes", "article_chunks")
ORDER = ("users", "categories", "tags", "columns", "articles", "article_tags",
         "comments", "settings", "links", "images", "audit_logs", "article_metrics", "article_chunks")


def same_database(left, right):
    left, right = make_url(left), make_url(right)
    if left.get_backend_name() != right.get_backend_name():
        return False
    if left.get_backend_name() == "sqlite":
        return Path(left.database or "").resolve() == Path(right.database or "").resolve()
    hosts = {"localhost": "127.0.0.1", None: "127.0.0.1"}
    return (hosts.get(left.host, left.host), left.port or 3306, left.database) == (hosts.get(right.host, right.host), right.port or 3306, right.database)


def read_source(connection):
    metadata = MetaData()
    existing = set(inspect(connection).get_table_names())
    missing = set(REQUIRED) - existing
    if missing:
        raise ImportErrorWithContext("Go 数据库缺少必需表: " + ", ".join(sorted(missing)))
    result = {}
    for name in (*REQUIRED, *OPTIONAL):
        if name not in existing:
            result[name] = []
            continue
        table = Table(name, metadata, autoload_with=connection)
        absent = set(REQUIRED.get(name, OPTIONAL_COLUMNS.get(name, ()))) - set(table.c.keys())
        if absent:
            raise ImportErrorWithContext(f"Go 表 {name} 缺少字段: {', '.join(sorted(absent))}")
        result[name] = [dict(row) for row in connection.execute(select(table)).mappings()]
    return result


def copy_columns(table_name, source_row, aliases=None):
    aliases = aliases or {}
    table = Base.metadata.tables[table_name]
    result = {}
    for column in table.columns:
        source_name = aliases.get(column.name, column.name)
        if source_name in source_row and source_row[source_name] is not None:
            result[column.name] = source_row[source_name]
    return result


def ordered_comments(rows):
    remaining = {row["id"]: row for row in rows}
    complete, result = set(), []
    while remaining:
        ready = [row for row in remaining.values() if not row.get("parent_id") or row["parent_id"] in complete]
        if not ready:
            raise ImportErrorWithContext("评论父子关系存在缺失或循环，无法安全导入")
        for row in ready:
            result.append(row)
            complete.add(row["id"])
            del remaining[row["id"]]
    return result


def build_plan(source):
    plan = {name: [] for name in ORDER}
    skipped = Counter()
    for name in ("users", "categories", "tags", "columns", "articles", "comments", "links", "images", "audit_logs"):
        for row in source[name]:
            if name in {"images", "audit_logs"} and row.get("deleted_at"):
                skipped[f"{name}_deleted"] += 1
                continue
            aliases = {"password_hash": "password"} if name == "users" else None
            record = copy_columns(name, row, aliases)
            for nullable_id in ("column_id", "user_id", "parent_id"):
                if nullable_id in record and record[nullable_id] == 0:
                    record[nullable_id] = None
            if name == "users" and not BcryptHasher.identify(record.get("password_hash", "")):
                raise ImportErrorWithContext(f"用户 ID {record.get('id')} 的密码不是可识别的 bcrypt 哈希")
            if name == "articles":
                record["password"] = hash_password(str(row["password"])) if row.get("password") else ""
            plan[name].append(record)
    plan["comments"] = ordered_comments(plan["comments"])
    plan["settings"] = [{"key": row["k"], "value": row.get("v") or ""} for row in source["settings"]]
    plan["article_tags"] = [copy_columns("article_tags", row) for row in source["article_tags"]]

    metrics = {}
    for source_name, kind in (("article_views", "view"), ("article_likes", "like")):
        for row in source[source_name]:
            if row.get("deleted_at"):
                skipped[f"{source_name}_deleted"] += 1
                continue
            identity = f"user:{row['user_id']}" if kind == "like" and row.get("user_id") else f"ip:{row.get('ip') or ''}"
            digest = hashlib.sha256(identity.encode()).hexdigest()
            key = (row["article_id"], kind, digest)
            timestamp = row.get("viewed_at") or row.get("created_at") or datetime.utcnow()
            if key in metrics:
                skipped[f"{source_name}_duplicate"] += 1
                if metrics[key]["created_at"] >= timestamp:
                    continue
            metrics[key] = {"article_id": key[0], "kind": kind, "fingerprint": digest, "created_at": timestamp}
    plan["article_metrics"] = list(metrics.values())

    articles = {row["id"]: row for row in source["articles"]}
    for row in source["article_chunks"]:
        article = articles.get(row.get("article_id"))
        blob = row.get("embedding") or b""
        if not article or article.get("status") != 1 or article.get("password") or article.get("deleted_at") or (article.get("publish_at") and article["publish_at"] > datetime.utcnow()):
            skipped["article_chunks_nonpublic"] += 1
            continue
        if not blob or len(blob) % 4:
            raise ImportErrorWithContext(f"文章 ID {row.get('article_id')} 的向量数据格式无效")
        vector = struct.unpack(f"<{len(blob)//4}f", blob)
        if not all(math.isfinite(value) for value in vector) or (row.get("embedding_dim") and row["embedding_dim"] != len(vector)):
            raise ImportErrorWithContext(f"文章 ID {row.get('article_id')} 的向量维度或数值无效")
        record = copy_columns("article_chunks", row)
        record["embedding"] = json.dumps(vector).encode()
        record["embedding_dim"] = len(vector)
        plan["article_chunks"].append(record)
    validate_plan(plan)
    return plan, skipped


def validate_plan(plan):
    for table, columns in (("users", ("username", "email")), ("categories", ("name",)), ("tags", ("name",))):
        for column in columns:
            values = [str(row.get(column, "")).casefold() for row in plan[table]]
            if not all(values) or len(values) != len(set(values)):
                raise ImportErrorWithContext(f"{table}.{column} 存在空值或重复值，需先人工处理")
    ids = {name: {row["id"] for row in rows} for name, rows in plan.items() if rows and "id" in rows[0]}
    for name in ORDER:
        table = Base.metadata.tables[name]
        for row in plan[name]:
            for fk in table.foreign_keys:
                value = row.get(fk.parent.name)
                if value is not None and value not in ids.get(fk.column.table.name, set()):
                    raise ImportErrorWithContext(f"{name}.{fk.parent.name} 引用不存在的 {fk.column.table.name} ID {value}")
    comments = {row["id"]: row for row in plan["comments"]}
    for row in plan["comments"]:
        parent = comments.get(row.get("parent_id"))
        if parent and parent["article_id"] != row["article_id"]:
            raise ImportErrorWithContext(f"评论 ID {row['id']} 的父评论属于其他文章")


def ensure_empty(connection):
    inspector = inspect(connection)
    missing = (set(ORDER) | {"refresh_tokens", "alembic_version"}) - set(inspector.get_table_names())
    if missing:
        raise ImportErrorWithContext("目标数据库尚未完成 Alembic 迁移")
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "app" / "db" / "migrations"))
    expected = ScriptDirectory.from_config(config).get_current_head()
    versions = Table("alembic_version", MetaData(), autoload_with=connection)
    current = connection.scalar(select(versions.c.version_num))
    if current != expected:
        raise ImportErrorWithContext("目标数据库版本不是最新，请先执行 alembic upgrade head")
    for name in (*ORDER, "refresh_tokens"):
        if connection.scalar(select(func.count()).select_from(Base.metadata.tables[name])):
            raise ImportErrorWithContext(f"目标表 {name} 非空；请使用已迁移但尚未初始化的空库")


def import_database(source_url, target_url, apply=False):
    source_engine = target_engine = None
    try:
        if same_database(source_url, target_url):
            raise ImportErrorWithContext("源数据库和目标数据库不能相同")
        source_engine = create_engine(source_url, pool_pre_ping=True)
        target_engine = create_engine(target_url, pool_pre_ping=True)
        with source_engine.connect() as source, target_engine.connect() as target:
            ensure_empty(target)
            source_data = read_source(source)
            plan, skipped = build_plan(source_data)
        if apply:
            with target_engine.begin() as target:
                ensure_empty(target)
                for name in ORDER:
                    table, records = Base.metadata.tables[name], plan[name]
                    if name == "comments":
                        # Preserve parent-before-child ordering across self-references.
                        for record in records:
                            target.execute(table.insert(), record)
                    else:
                        groups = defaultdict(list)
                        for record in records:
                            groups[tuple(sorted(record))].append(record)
                        for group in groups.values():
                            for offset in range(0, len(group), 100):
                                target.execute(table.insert(), group[offset:offset + 100])
                    actual = target.scalar(select(func.count()).select_from(table))
                    if actual != len(records):
                        raise ImportErrorWithContext(f"导入后 {name} 数量不一致，事务已回滚")
        return {"source": {name: len(rows) for name, rows in source_data.items()},
                "planned": {name: len(rows) for name, rows in plan.items()}, "skipped": dict(skipped), "applied": apply}
    except (SQLAlchemyError, ValueError) as exc:
        # SQLAlchemy exception strings can include source rows and password hashes.
        raise ImportErrorWithContext(f"数据库操作失败（{type(exc).__name__}），导入事务未提交") from exc
    finally:
        if source_engine is not None:
            source_engine.dispose()
        if target_engine is not None:
            target_engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="将验证后的数据写入空的目标数据库")
    args = parser.parse_args()
    source_url = os.environ.get("BLOG_GO_DATABASE_URL")
    if not source_url:
        parser.error("请通过环境变量 BLOG_GO_DATABASE_URL 指定 Go 数据库")
    try:
        result = import_database(source_url, settings.database_url, apply=args.apply)
    except ImportErrorWithContext as exc:
        parser.exit(1, f"导入中止: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
