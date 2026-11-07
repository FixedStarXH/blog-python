from io import BytesIO
import logging
from pathlib import Path
import secrets

from PIL import Image as PILImage
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import DomainError
from app.models import Image
from app.storage.files import resolve_upload_path

logger = logging.getLogger(__name__)
MAX_UPLOAD_SIZE = 5 * 1024 * 1024
MAX_PIXELS = 25_000_000
FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".gif": "GIF", ".webp": "WEBP"}
SIGNATURES = {".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff", ".png": b"\x89PNG\r\n\x1a\n", ".gif": b"GIF8", ".webp": b"RIFF"}


def _original_name(raw_name: str | None) -> tuple[str, str]:
    name = (raw_name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable() and ch not in "/\\").strip()
    extension = Path(name).suffix.lower()
    if extension not in FORMATS:
        raise DomainError("unsupported image type", 415)
    return name[:-len(extension)][:255 - len(extension)] + name[-len(extension):], extension


def _validate_image(data: bytes, extension: str) -> None:
    if len(data) > MAX_UPLOAD_SIZE:
        raise DomainError("image exceeds 5 MiB", 413)
    if not data.startswith(SIGNATURES[extension]):
        raise DomainError("image extension does not match its content", 415)
    try:
        with PILImage.open(BytesIO(data)) as image:
            if image.format != FORMATS[extension]:
                raise DomainError("image extension does not match its content", 415)
            if image.width * image.height > MAX_PIXELS:
                raise DomainError("image dimensions are too large", 413)
            image.verify()
    except DomainError:
        raise
    except Exception as exc:
        raise DomainError("image content is corrupt", 415) from exc


def save_image(db: Session, raw_name: str | None, data: bytes, uploader_id: int) -> dict:
    original_name, extension = _original_name(raw_name)
    _validate_image(data, extension)
    root = Path(settings.upload_dir).resolve()
    target = None
    created = None
    try:
        root.mkdir(parents=True, exist_ok=True)
        for _ in range(3):
            candidate = root / (secrets.token_hex(16) + extension)
            try:
                with candidate.open("xb") as output:
                    created = candidate
                    output.write(data)
                target = candidate
                break
            except FileExistsError:
                continue
        else:
            raise OSError("unable to allocate upload filename")
    except OSError as exc:
        if created is not None:
            _remove_quietly(created)
        logger.exception("unable to write uploaded image")
        raise DomainError("unable to save image", 500) from exc

    record = Image(name=original_name, url="/uploads/" + target.name,
                   size=len(data), ext=extension.lstrip("."), uploader_id=uploader_id)
    image_url = record.url
    try:
        db.add(record)
        db.flush()
        image_id = record.id
        db.commit()
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            logger.exception("unable to roll back failed image upload")
        _remove_quietly(target)
        logger.exception("unable to save uploaded image metadata")
        raise DomainError("unable to save image metadata", 500) from exc
    return {"id": image_id, "url": image_url, "name": original_name, "size": len(data),
            "ext": extension.lstrip("."), "uploaderId": uploader_id}


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.exception("unable to remove image file after failed operation")


def delete_image(db: Session, image_id: int) -> None:
    record = db.scalar(select(Image).where(Image.id == image_id).with_for_update())
    if record is None:
        raise DomainError("image not found", 404)
    try:
        target = resolve_upload_path(record.url, settings.upload_dir)
    except ValueError as exc:
        raise DomainError("invalid stored upload path", 400) from exc
    if target.exists() and not target.is_file():
        raise DomainError("invalid stored upload path", 400)

    staged = None
    if target.exists():
        root = Path(settings.upload_dir).resolve()
        for _ in range(3):
            candidate = root / (".deleting-" + secrets.token_hex(16))
            if not candidate.exists():
                staged = candidate
                break
        if staged is None:
            raise DomainError("unable to remove image", 500)
        try:
            target.rename(staged)
        except FileNotFoundError:
            staged = None
        except OSError as exc:
            logger.exception("unable to stage image deletion")
            raise DomainError("unable to remove image", 500) from exc
    try:
        db.delete(record)
        db.commit()
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            logger.exception("unable to roll back failed image deletion")
        if staged is not None:
            try:
                staged.rename(target)
            except OSError:
                logger.exception("unable to restore image after database rollback")
        logger.exception("unable to delete image metadata")
        raise DomainError("unable to remove image", 500) from exc
    if staged is not None:
        try:
            staged.unlink(missing_ok=True)
        except OSError:
            logger.exception("unable to remove staged image after database commit")
