from pathlib import Path
from urllib.parse import unquote, urlsplit


def remove_upload(url: str, upload_dir: str) -> None:
    """Remove a local upload addressed by its URL, keeping paths inside upload_dir."""
    path = unquote(urlsplit(url).path)
    if not path.startswith("/uploads/"):
        return
    root = Path(upload_dir).resolve()
    target = (root / path[len("/uploads/"):]).resolve()
    if root not in target.parents:
        raise ValueError("invalid upload path")
    target.unlink(missing_ok=True)
