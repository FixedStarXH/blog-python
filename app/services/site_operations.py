"""Site settings and friend link use cases with explicit transaction ownership."""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.models import Link, SiteSetting

EDITABLE_SETTINGS = {
    "site_title", "site_subtitle", "site_description", "site_logo",
    "site_beian", "social_github", "social_email",
}


def read_settings(db: Session) -> dict:
    values = {
        item.key: item.value
        for item in db.scalars(select(SiteSetting)).all()
        if item.key in EDITABLE_SETTINGS
    }
    quote_setting = db.get(SiteSetting, "daily_quotes")
    values["quotes"] = [
        {
            "content": part.split("|", 1)[0].strip(),
            "author": part.split("|", 1)[1].strip()
            if "|" in part and part.split("|", 1)[1].strip() else "佚名",
        }
        for part in (quote_setting.value if quote_setting else "").splitlines()
        if part.strip()
    ]
    return values


def update_settings(db: Session, values: dict) -> None:
    for key, value in values.items():
        if key not in EDITABLE_SETTINGS and key != "daily_quotes":
            raise DomainError("setting is not editable", 400)
        item = db.get(SiteSetting, key)
        if item:
            item.value = str(value)
        else:
            db.add(SiteSetting(key=key, value=str(value)))
    db.commit()


def list_links(db: Session) -> list[Link]:
    return list(db.scalars(
        select(Link).where(Link.deleted_at.is_(None)).order_by(Link.sort, Link.id)
    ).all())


def create_link(db: Session, values: dict) -> Link:
    link = Link(**values)
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


def update_link(db: Session, item_id: int, values: dict) -> Link:
    link = db.scalar(select(Link).where(Link.id == item_id, Link.deleted_at.is_(None)))
    if link is None:
        raise DomainError("link not found", 404)
    for key, value in values.items():
        setattr(link, key, value)
    db.commit()
    return link


def delete_link(db: Session, item_id: int) -> None:
    link = db.scalar(select(Link).where(Link.id == item_id, Link.deleted_at.is_(None)))
    if link is None:
        raise DomainError("link not found", 404)
    link.deleted_at = datetime.utcnow()
    db.commit()
