"""Sanitize article HTML at the public rendering boundary."""
import re

import nh3
from markdownify import markdownify


def _filter_attribute(tag: str, attribute: str, value: str) -> str | None:
    if tag == "code" and attribute == "class":
        classes = [item for item in value.split() if re.fullmatch(r"language-[a-zA-Z0-9_+-]+", item)]
        return " ".join(classes) or None
    return value


_attributes = {tag: set(names) for tag, names in nh3.ALLOWED_ATTRIBUTES.items()}
_attributes["code"] = {"class"}
_cleaner = nh3.Cleaner(
    attributes=_attributes,
    attribute_filter=_filter_attribute,
    url_schemes={"http", "https", "mailto"},
)


def sanitize_article_html(value: str) -> str:
    return _cleaner.clean(value)


def _code_language(pre):
    code = pre.find("code", recursive=False)
    if code:
        for name in code.get("class", []):
            if name.startswith("language-"):
                return name.removeprefix("language-")
    return ""


def article_markdown(value: str) -> str:
    content = markdownify(sanitize_article_html(value), heading_style="ATX", bullets="-",
                          code_language_callback=_code_language, bs4_options={"features": "html.parser"})
    return content.rstrip() + "\n" if content.strip() else ""
