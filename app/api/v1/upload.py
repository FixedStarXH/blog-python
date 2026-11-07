from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session
from app.api.deps import current_user, get_db
from app.services import images as image_service
from app.core.errors import envelope
from app.models import User

router=APIRouter()


@router.post("/upload")
async def upload(file: UploadFile=File(...),db: Session=Depends(get_db),user: User=Depends(current_user)):
    data = await file.read(image_service.MAX_UPLOAD_SIZE + 1)
    return envelope(image_service.save_image(db, file.filename, data, user.id))
