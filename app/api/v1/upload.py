from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session
from app.api.deps import current_user, get_db
from app.api.v1.admin_operations import upload as persist_image
from app.models import User

router=APIRouter()


@router.post("/upload")
async def upload(file: UploadFile=File(...),db: Session=Depends(get_db),user: User=Depends(current_user)):
    return await persist_image(file,db,user)
