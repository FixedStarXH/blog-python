from pathlib import Path
from urllib.parse import unquote, urlsplit


def resolve_upload_path(url: str, upload_dir: str) -> Path:
    """Resolve a stored local upload URL without leaving the upload volume."""
    parts = urlsplit(url)
    path = unquote(parts.path)
    if parts.scheme or parts.netloc or not path.startswith("/uploads/"):
        raise ValueError("invalid upload path")
    root = Path(upload_dir).resolve()
    target = (root / path[len("/uploads/"):]).resolve()
    if root not in target.parents:
        raise ValueError("invalid upload path")
    return target


def remove_upload(url: str, upload_dir: str) -> None:
    target = resolve_upload_path(url, upload_dir)
    target.unlink(missing_ok=True)
