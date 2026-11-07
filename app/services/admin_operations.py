"""Read and write use cases for admin operations."""
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.articles import article_data
from app.core.errors import DomainError
from app.models import Article, AuditLog, Comment, MetricEvent, User


def dashboard(db: Session) -> dict:
    hot = db.scalars(select(Article).where(
        Article.status == 1, Article.deleted_at.is_(None), Article.password == ""
    ).order_by(Article.view_count.desc(), Article.id.desc()).limit(5)).all()
    recent = db.scalars(select(Comment).where(Comment.deleted_at.is_(None)).order_by(
        Comment.created_at.desc(), Comment.id.desc()
    ).limit(5)).all()
    counts = {
        "articles": db.scalar(select(func.count(Article.id)).where(Article.deleted_at.is_(None))) or 0,
        "users": db.scalar(select(func.count(User.id)).where(User.deleted_at.is_(None))) or 0,
        "views": db.scalar(select(func.coalesce(func.sum(Article.view_count), 0)).where(Article.deleted_at.is_(None))) or 0,
        "comments": db.scalar(select(func.count(Comment.id)).where(Comment.deleted_at.is_(None))) or 0,
        "pendingComments": db.scalar(select(func.count(Comment.id)).where(
            Comment.status == 0, Comment.deleted_at.is_(None)
        )) or 0,
    }
    return {
        "counts": counts,
        "hotArticles": [article_data(article, False) for article in hot],
        "recentComments": [{
            "id": comment.id, "content": comment.content, "nickname": comment.nickname,
            "status": comment.status, "createdAt": comment.created_at.isoformat(),
        } for comment in recent],
    }


def views_trend(db: Session, days: int) -> list[dict]:
    start_day = date.today() - timedelta(days=days - 1)
    since = datetime.combine(start_day, time.min)
    rows = db.execute(select(func.date(MetricEvent.created_at), func.count(MetricEvent.id)).where(
        MetricEvent.created_at >= since, MetricEvent.kind == "view"
    ).group_by(func.date(MetricEvent.created_at))).all()
    counts = {str(day): count for day, count in rows}
    return [{
        "date": (start_day + timedelta(days=offset)).isoformat(),
        "views": counts.get((start_day + timedelta(days=offset)).isoformat(), 0),
    } for offset in range(days)]


def audit_logs(db: Session, page: int, page_size: int, action: str | None,
               status: int | None, operator_id: int | None, target_type: str | None) -> dict:
    filters = []
    if action:
        filters.append(AuditLog.action == action)
    if status is not None:
        filters.append(AuditLog.status == status)
    if operator_id is not None:
        filters.append(AuditLog.operator_id == operator_id)
    if target_type:
        filters.append(AuditLog.target_type == target_type)
    query = select(AuditLog).where(*filters)
    total = db.scalar(select(func.count()).select_from(AuditLog).where(*filters)) or 0
    rows = db.scalars(query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).offset(
        (page - 1) * page_size
    ).limit(page_size)).all()
    role_names = {0: "未认证", 1: "用户", 2: "编辑", 3: "管理员"}
    recent_actions = db.scalars(select(AuditLog.action).order_by(
        AuditLog.created_at.desc(), AuditLog.id.desc()
    ).limit(1000)).all()
    return {
        "list": [{
            "id": row.id, "operatorId": row.operator_id, "username": row.username,
            "role": row.role, "roleName": role_names.get(row.role, "未知"),
            "action": row.action, "method": row.method, "path": row.path,
            "targetType": row.target_type, "targetId": row.target_id,
            "detail": row.detail, "status": row.status, "ip": row.ip,
            "createdAt": row.created_at.isoformat(),
        } for row in rows],
        "total": total, "page": page, "pageSize": page_size,
        "actions": sorted(set(recent_actions)),
    }


def clear_audit_logs(db: Session) -> None:
    try:
        db.query(AuditLog).delete()
        db.commit()
    except Exception as exc:
        db.rollback()
        raise DomainError("unable to clear audit logs", 500) from exc
