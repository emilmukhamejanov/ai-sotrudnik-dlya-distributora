"""Маскирование персональных данных до отправки во внешнюю модель.

Положение о ПДн компании, п. 3.3: ИИН, телефоны и e-mail заменяются условными метками перед передачей
в облачные ИИ-модели; п. 3.4: ассистент не выводит ИИН и телефоны покупателей.

  * Входящее сообщение: ПДн → метки [ИИН_1], [ТЕЛЕФОН_1], [EMAIL_1], [IBAN_1]. Модель, трасса и история
    диалога видят только метки. Номер карты заменяется необратимо — его не получит никто.
  * Реальные значения подставляются обратно только там, где они нужны людям: в ответе пользователю
    (это его же данные) и в подтверждённом действии Битрикс24.
  * Ответ модели дополнительно проверяется: ИИН и телефоны, которых не было в сообщении пользователя,
    скрываются (например, если такие данные оказались во фрагменте документа).

БИН юрлица (5-я цифра 4–6) — открытые сведения о компании, его не маскируем.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .demo_data.kz_ids import is_valid

_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.[a-zа-я]{2,}(?![\w-])", re.I)
_IBAN = re.compile(r"(?<![A-Z0-9])KZ\d{2}(?:\s?[A-Z0-9]){16}(?![A-Z0-9])", re.I)
# Номер карты: 13–19 цифр, возможно группами через пробел или дефис; проверяем по Луну.
_CARD = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")
# Телефоны Казахстана: +7 / 8 / 7, код 7xx (мобильные и городские 727, 717, 725…), 7 цифр номера.
_PHONE = re.compile(r"(?<![\d+])(?:\+?7|8)[\s\-(]*7\d{2}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)")
_ID12 = re.compile(r"(?<!\d)\d{12}(?!\d)")

KIND_LABELS = {"iin": "ИИН", "phone": "ТЕЛЕФОН", "email": "EMAIL", "iban": "IBAN"}
CARD_MASK = "[КАРТА скрыта]"


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _is_company_bin(number: str) -> bool:
    return is_valid(number) and number[4] in "456"


def _canonical(kind: str, value: str) -> str:
    if kind in ("phone", "iin"):
        digits = re.sub(r"\D", "", value)
        return digits[-10:] if kind == "phone" else digits
    if kind == "iban":
        return re.sub(r"\s", "", value).upper()
    return value.lower()


@dataclass
class PiiVault:
    """Соответствие «метка → значение» в пределах одного диалога."""

    values: dict[str, str] = field(default_factory=dict)  # "[ТЕЛЕФОН_1]" → "+7 701 123 45 67"
    _by_canonical: dict[str, str] = field(default_factory=dict)  # "phone:7011234567" → "[ТЕЛЕФОН_1]"

    @classmethod
    def from_dict(cls, data: dict | None) -> PiiVault:
        vault = cls()
        for placeholder, value in (data or {}).items():
            vault.values[placeholder] = value
            kind = next(k for k, label in KIND_LABELS.items() if placeholder.startswith(f"[{label}_"))
            vault._by_canonical[f"{kind}:{_canonical(kind, value)}"] = placeholder
        return vault

    def to_dict(self) -> dict[str, str]:
        return dict(self.values)

    def _placeholder(self, kind: str, value: str) -> str:
        key = f"{kind}:{_canonical(kind, value)}"
        if key not in self._by_canonical:
            n = sum(1 for p in self.values if p.startswith(f"[{KIND_LABELS[kind]}_")) + 1
            placeholder = f"[{KIND_LABELS[kind]}_{n}]"
            self._by_canonical[key] = placeholder
            self.values[placeholder] = value
        return self._by_canonical[key]

    def mask(self, text: str) -> tuple[str, dict[str, int]]:
        """Возвращает текст с метками и счётчик найденного по видам (без самих значений)."""
        found: dict[str, int] = {}

        def count(kind: str) -> None:
            found[kind] = found.get(kind, 0) + 1

        def sub_email(m: re.Match) -> str:
            count("email")
            return self._placeholder("email", m.group(0))

        def sub_iban(m: re.Match) -> str:
            count("iban")
            return self._placeholder("iban", m.group(0))

        def sub_card(m: re.Match) -> str:
            digits = re.sub(r"\D", "", m.group(0))
            if 13 <= len(digits) <= 19 and _luhn(digits) and not _ID12.fullmatch(digits):
                count("card")
                return CARD_MASK
            return m.group(0)

        def sub_phone(m: re.Match) -> str:
            count("phone")
            return self._placeholder("phone", m.group(0))

        def sub_id(m: re.Match) -> str:
            if _is_company_bin(m.group(0)):
                return m.group(0)
            count("iin")
            return self._placeholder("iin", m.group(0))

        text = _EMAIL.sub(sub_email, text)
        text = _IBAN.sub(sub_iban, text)
        text = _CARD.sub(sub_card, text)
        text = _PHONE.sub(sub_phone, text)
        text = _ID12.sub(sub_id, text)
        return text, found

    def unmask(self, text: str) -> str:
        for placeholder, value in self.values.items():
            text = text.replace(placeholder, value)
        return text


def redact_output(text: str, vault: PiiVault) -> tuple[str, int]:
    """Скрывает ИИН, телефоны и карты в ответе модели, если пользователь сам их не присылал."""
    hidden = 0
    known = set(vault._by_canonical)

    def sub_phone(m: re.Match) -> str:
        nonlocal hidden
        if f"phone:{_canonical('phone', m.group(0))}" in known:
            return m.group(0)
        hidden += 1
        return "[телефон скрыт — см. карточку клиента в 1С]"

    def sub_id(m: re.Match) -> str:
        nonlocal hidden
        if _is_company_bin(m.group(0)) or f"iin:{m.group(0)}" in known:
            return m.group(0)
        hidden += 1
        return "[ИИН скрыт]"

    def sub_card(m: re.Match) -> str:
        nonlocal hidden
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits) and not _ID12.fullmatch(digits):
            hidden += 1
            return CARD_MASK
        return m.group(0)

    text = _CARD.sub(sub_card, text)
    text = _PHONE.sub(sub_phone, text)
    text = _ID12.sub(sub_id, text)
    return text, hidden
