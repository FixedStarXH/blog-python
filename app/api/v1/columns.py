from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.deps import current_user, get_db
from app.core.errors import DomainError, envelope
from app.models import ColumnModel, User

router=APIRouter()


def shape(x): return {"id":x.id,"name":x.name,"description":x.description,"cover":x.cover,"authorId":x.author_id,"status":x.status}


@router.get("/columns")
def public_columns(db: Session=Depends(get_db)):
    return envelope([shape(x) for x in db.scalars(select(ColumnModel).where(ColumnModel.status==1,ColumnModel.deleted_at.is_(None)).order_by(ColumnModel.id.desc())).all()])


@router.get("/my/columns")
def my_columns(db: Session=Depends(get_db),user: User=Depends(current_user)):
    return envelope([shape(x) for x in db.scalars(select(ColumnModel).where(ColumnModel.author_id==user.id,ColumnModel.deleted_at.is_(None)).order_by(ColumnModel.id.desc())).all()])


@router.post("/my/columns",status_code=201)
def create_column(body: dict,db: Session=Depends(get_db),user: User=Depends(current_user)):
    name=str(body.get("name","")).strip()
    if not name: raise DomainError("column name is required")
    x=ColumnModel(name=name,description=str(body.get("description","")),cover=str(body.get("cover","")),author_id=user.id,status=1)
    db.add(x);db.commit();db.refresh(x);return envelope(shape(x))


@router.put("/my/columns/{column_id}")
def update_column(column_id: int,body: dict,db: Session=Depends(get_db),user: User=Depends(current_user)):
    x=db.scalar(select(ColumnModel).where(ColumnModel.id==column_id,ColumnModel.author_id==user.id,ColumnModel.deleted_at.is_(None)))
    if not x: raise DomainError("column not found",404)
    for key in ("name","description","cover"):
        if key in body: setattr(x,key,str(body[key]))
    db.commit();return envelope(shape(x))


@router.delete("/my/columns/{column_id}")
def delete_column(column_id: int,db: Session=Depends(get_db),user: User=Depends(current_user)):
    x=db.scalar(select(ColumnModel).where(ColumnModel.id==column_id,ColumnModel.author_id==user.id,ColumnModel.deleted_at.is_(None)))
    if not x: raise DomainError("column not found",404)
    x.deleted_at=datetime.utcnow();db.commit();return envelope({})
