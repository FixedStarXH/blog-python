from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from app.api.deps import current_user, get_db
from app.core.errors import DomainError, envelope
from app.models import Article, ColumnModel, User
from app.schemas.common import ColumnInput, ColumnUpdate
from app.services.taxonomy import lock_column

router=APIRouter()


def shape(x): return {"id":x.id,"name":x.name,"description":x.description,"cover":x.cover,"authorId":x.author_id,"status":x.status}


@router.get("/columns")
def public_columns(db: Session=Depends(get_db)):
    return envelope([shape(x) for x in db.scalars(select(ColumnModel).where(ColumnModel.status==1,ColumnModel.deleted_at.is_(None)).order_by(ColumnModel.id.desc())).all()])


@router.get("/my/columns")
def my_columns(db: Session=Depends(get_db),user: User=Depends(current_user)):
    return envelope([shape(x) for x in db.scalars(select(ColumnModel).where(ColumnModel.author_id==user.id,ColumnModel.deleted_at.is_(None)).order_by(ColumnModel.id.desc())).all()])


@router.post("/my/columns",status_code=201)
def create_column(body: ColumnInput,db: Session=Depends(get_db),user: User=Depends(current_user)):
    x=ColumnModel(name=body.name, description=body.description, cover=body.cover, author_id=user.id, status=body.status)
    db.add(x);db.commit();db.refresh(x);return envelope(shape(x))


@router.put("/my/columns/{column_id}")
def update_column(column_id: int,body: ColumnUpdate,db: Session=Depends(get_db),user: User=Depends(current_user)):
    x=db.scalar(select(ColumnModel).where(ColumnModel.id==column_id,ColumnModel.author_id==user.id,ColumnModel.deleted_at.is_(None)))
    if not x: raise DomainError("column not found",404)
    for key, value in body.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(x, key, value)
    db.commit();return envelope(shape(x))


@router.delete("/my/columns/{column_id}")
def delete_column(column_id: int,db: Session=Depends(get_db),user: User=Depends(current_user)):
    try:
        lock_column(db, column_id, user.id)
    except DomainError as exc:
        raise DomainError("column not found", 404) from exc
    if db.scalar(select(Article.id).where(Article.column_id == column_id).limit(1).with_for_update()):
        raise DomainError("column is in use, including by trashed articles", 409)
    db.execute(update(ColumnModel).where(ColumnModel.id == column_id).values(deleted_at=datetime.utcnow()))
    db.commit();return envelope({})
