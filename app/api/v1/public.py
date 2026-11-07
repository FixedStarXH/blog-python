from datetime import datetime
from collections import defaultdict
from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import extract, func, select
from sqlalchemy.orm import Session
from app.api.deps import get_db
from app.core.config import settings
from app.core.errors import envelope
from app.models import Article, Category, Link, SiteSetting, Tag, User
from app.core.sensitive import SENSITIVE_WORDS
from app.repositories.articles import public_query, visible_conditions

router = APIRouter()


@router.get("/archives")
@router.get("/archive")
def archives(db: Session = Depends(get_db)):
    rows = db.execute(select(Article.id, Article.title, Article.created_at).where(
        *visible_conditions(), Article.password == ""
    ).order_by(Article.created_at.desc(), Article.id.desc())).all()
    groups = defaultdict(list)
    for article in rows:
        groups[str(article.created_at.year)].append({
            "id": article.id, "title": article.title, "createdAt": article.created_at.isoformat()
        })
    return envelope([{"year": year, "count": len(items), "articles": items}
                     for year, items in sorted(groups.items(), reverse=True)])


@router.get("/settings")
def public_settings(db: Session = Depends(get_db)):
    public_keys={"site_title","site_subtitle","site_description","site_logo","site_beian","social_github","social_email"}
    values = {x.key:x.value for x in db.scalars(select(SiteSetting)).all() if x.key in public_keys}
    return envelope(values)


@router.get("/site")
def site_info(db: Session = Depends(get_db)):
    settings_data = public_settings(db)["data"]
    public_filters = (*visible_conditions(), Article.password == "")
    count=db.scalar(select(func.count(Article.id)).where(*public_filters)) or 0
    views=db.scalar(select(func.coalesce(func.sum(Article.view_count),0)).where(*public_filters)) or 0
    earliest=db.scalar(select(func.min(Article.created_at)).where(*public_filters))
    settings_data.update({"title":settings_data.get("site_title",""),"github":settings_data.get("social_github",""),"email":settings_data.get("social_email",""),"stats":{"articleCount":count,"viewCount":views,"foundYear":earliest.year if earliest else datetime.utcnow().year}})
    return envelope(settings_data)


def _quote_lines(db: Session) -> list[str]:
    item = db.get(SiteSetting,"daily_quotes")
    line=item.value if item and item.value else "把每一个今天都当作新的开始。|佚名"
    return [x.strip() for x in line.splitlines() if x.strip()] or [line]


@router.get("/quote")
def daily_quote(db: Session = Depends(get_db)):
    # Legacy Go contract: data is the selected raw quote string.
    import random
    return envelope(random.choice(_quote_lines(db)))


@router.get("/quote/random")
def quote(db: Session = Depends(get_db)):
    import random
    selected=random.choice(_quote_lines(db)); parts=selected.split("|",1)
    return envelope({"content":parts[0].strip(),"author":parts[1].strip() if len(parts)>1 and parts[1].strip() else "佚名"})


@router.get("/links")
def public_links(db: Session = Depends(get_db)):
    rows=db.scalars(select(Link).where(Link.enabled.is_(True),Link.deleted_at.is_(None)).order_by(Link.sort)).all()
    return envelope([{"id":x.id,"name":x.name,"url":x.url,"description":x.description} for x in rows])


@router.get("/sensitive/words")
def sensitive_words():
    return envelope({"words":SENSITIVE_WORDS})


@router.get("/rss.xml")
def rss(db: Session = Depends(get_db)):
    rows=db.scalars(public_query(db).order_by(Article.publish_at.desc(),Article.id.desc()).limit(20)).all()
    base=_xml(settings.public_url.rstrip("/"))
    items="".join(f"<item><title>{_xml(a.title)}</title><link>{base}/article.html?id={a.id}</link><guid isPermaLink=\"true\">{base}/article.html?id={a.id}</guid><description>{_xml(a.summary)}</description><pubDate>{(a.publish_at or a.created_at).strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate></item>" for a in rows)
    site_title=db.get(SiteSetting,"site_title")
    title=_xml(site_title.value if site_title and site_title.value else "Blog")
    xml=f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{title}</title><link>{base}</link><description>{title}</description>{items}</channel></rss>'
    return Response(xml,media_type="application/rss+xml; charset=utf-8")


@router.get("/feed.xml")
def feed(db: Session = Depends(get_db)):
    return rss(db)


@router.get("/sitemap.xml")
def sitemap(db: Session = Depends(get_db)):
    rows=db.scalars(public_query(db).order_by(Article.publish_at.desc(),Article.id.desc())).all()
    base=_xml(settings.public_url.rstrip("/"))
    urls=f"<url><loc>{base}</loc><lastmod>{datetime.utcnow().date().isoformat()}</lastmod></url>"+"".join(f"<url><loc>{base}/article.html?id={a.id}</loc><lastmod>{(a.updated_at or a.created_at).date().isoformat()}</lastmod></url>" for a in rows)
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>',media_type="application/xml; charset=utf-8")


def _xml(value):
    import html
    return html.escape(value or "")
