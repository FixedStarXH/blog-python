from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models import Category, SiteSetting, User


def seed(db: Session):
    if not db.scalar(select(Category.id).limit(1)):
        db.add_all([Category(name="默认分类",sort=0),Category(name="技术",sort=10)])
    if not db.get(SiteSetting,"site_name"):
        db.add_all([SiteSetting(key="site_title",value="Blog"),SiteSetting(key="daily_quotes",value="把每一个今天都当作新的开始。 | 佚名")])
    if not db.scalar(select(User.id).where(User.username==settings.seed_username)):
        db.add(User(username=settings.seed_username,email=settings.seed_email,password_hash=hash_password(settings.seed_password),nickname="管理员",role=3,status=1))
    db.commit()


if __name__=="__main__":
    with SessionLocal() as session: seed(session)
