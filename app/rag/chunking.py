import re
import html


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value or ""))).strip()


def split_chunks(value: str, size: int = 500, overlap: int = 50) -> list[str]:
    text=clean_text(value)
    return [text[max(0,start-overlap):start+size] for start in range(0,len(text),size)] or ([text] if text else [])
