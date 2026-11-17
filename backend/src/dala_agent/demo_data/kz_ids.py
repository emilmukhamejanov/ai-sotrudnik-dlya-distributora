"""Генерация и проверка казахстанских ИИН/БИН (12 цифр, контрольный разряд).

Номера синтетические: формат и контрольная сумма корректны, чтобы маскирование
ПДн и валидаторы тестировались на правдоподобных данных, но к реальным людям
и организациям они не относятся.
"""

from __future__ import annotations

import datetime as dt
import random

_W1 = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)
_W2 = (3, 4, 5, 6, 7, 8, 9, 10, 11, 1, 2)


def check_digit(first11: str) -> int | None:
    """Контрольный разряд по алгоритму ИИН/БИН; None — если номер с такими цифрами невозможен."""
    digits = [int(c) for c in first11]
    s = sum(d * w for d, w in zip(digits, _W1, strict=True)) % 11
    if s == 10:
        s = sum(d * w for d, w in zip(digits, _W2, strict=True)) % 11
        if s == 10:
            return None
    return s


def is_valid(number: str) -> bool:
    if len(number) != 12 or not number.isdigit():
        return False
    cd = check_digit(number[:11])
    return cd is not None and cd == int(number[11])


def make_iin(rng: random.Random, birth: dt.date, male: bool) -> str:
    """ИИН физлица: ГГММДД + век/пол + 4 цифры + контрольная."""
    if birth.year < 2000:
        century = 3 if male else 4
    else:
        century = 5 if male else 6
    while True:
        body = f"{birth:%y%m%d}{century}{rng.randint(0, 9999):04d}"
        cd = check_digit(body)
        if cd is not None:
            return body + str(cd)


def make_bin(rng: random.Random, registered: dt.date) -> str:
    """БИН юрлица: ГГММ + тип (4 — резидент) + признак (0 — головное) + 5 цифр + контрольная."""
    while True:
        body = f"{registered:%y%m}40{rng.randint(0, 99999):05d}"
        cd = check_digit(body)
        if cd is not None:
            return body + str(cd)
