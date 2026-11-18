"""Защита от prompt injection в коде, а не только в промпте.

Документы (особенно внешние письма) и сообщения пользователя проверяются шаблонами:
  * из текста документов вырезаются скрытые HTML-комментарии и невидимые символы;
  * абзацы внешних документов, похожие на инструкции для ИИ, заменяются пометкой — модель их не видит
    и поэтому не может ни выполнить, ни пересказать;
  * сообщение пользователя с попыткой переписать правила агента отклоняется без вызова модели.

Шаблоны — эвристика: они ловят типовые атаки и дешёвые для проверки. Главная защита — по построению
(тексты документов не попадают в планировщик действий, действия только после «да» человека).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_INVISIBLE = re.compile(r"[​-‏‪-‮⁠-⁤﻿]")

# Признаки текста, адресованного модели, а не человеку (ru / kk / en).
_PATTERNS = [
    r"\b(игнорир\w*|не\s+учитывай|забудь)\b.{0,40}\b(инструкц\w*|правил\w*|указани\w*)",
    r"\b(предыдущие|прежние|все)\s+(инструкц\w*|указани\w*)",
    r"\bignore\b.{0,30}\b(instructions?|rules|prompt)\b",
    r"\bdisregard\b.{0,30}\b(instructions?|rules)\b",
    r"(^|\s|<!--\s*)(system|assistant|developer)\s*:",
    r"\b(системн\w*\s+(промпт|инструкц\w*|сообщени\w*))\b",
    r"\bsystem\s+prompt\b",
    r"\b(для|to)\s+(автоматическ\w*\s+систем\w*|ии|ai|ассистент\w*|модел\w*|бот\w*|llm)\b\s*[:!]",
    r"\bты\s+(теперь\s+)?(ии|ai)?-?\s*ассистент\w*\b",
    r"\byou\s+are\s+now\b",
    r"\b(режим\s+разработчика|developer\s+mode|jailbreak|dan\s+mode)\b",
    r"\bнұсқаулар\w*\s+елеме\w*",
    r"\bбез\s+подтверждения\s+пользовател\w*",
    r"\bwithout\s+(user\s+)?confirmation\b",
]
_RE = re.compile("|".join(f"(?:{p})" for p in _PATTERNS), re.I | re.M)

REMOVED_NOTE = "[абзац скрыт защитой: текст адресован ИИ, а не людям]"


def looks_like_injection(text: str) -> bool:
    return bool(_RE.search(text or ""))


@dataclass
class SanitizedText:
    text: str
    removed_comments: int = 0
    removed_paragraphs: list[str] = field(default_factory=list)

    @property
    def flagged(self) -> bool:
        return bool(self.removed_comments or self.removed_paragraphs)


def sanitize_document(text: str, *, external: bool) -> SanitizedText:
    """Чистит фрагмент документа перед тем, как показать его модели."""
    comments = _HTML_COMMENT.findall(text)
    clean = _INVISIBLE.sub("", _HTML_COMMENT.sub("", text))
    result = SanitizedText(text=clean, removed_comments=len(comments))
    if not external:
        return result  # внутренние регламенты не режем: их пишет компания, а ложное срабатывание дорого
    paragraphs = re.split(r"(\n\s*\n)", clean)
    kept = []
    for part in paragraphs:
        if part.strip() and looks_like_injection(part):
            result.removed_paragraphs.append(part.strip()[:200])
            kept.append(REMOVED_NOTE)
        else:
            kept.append(part)
    result.text = re.sub(r"\n{3,}", "\n\n", "".join(kept)).strip()
    return result


def is_prompt_attack(message: str) -> bool:
    """Сообщение пользователя пытается переписать правила агента или выведать системный промпт."""
    return looks_like_injection(message)
