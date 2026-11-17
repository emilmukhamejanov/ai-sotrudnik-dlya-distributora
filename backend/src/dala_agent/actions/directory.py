"""Справочник сотрудников для выбора ответственного: «поставь задачу Иванову» → конкретный человек."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Employee:
    id: int
    full_name: str
    position: str
    city: str

    @property
    def surname(self) -> str:
        return self.full_name.split()[0]

    @property
    def first_name(self) -> str:
        parts = self.full_name.split()
        return parts[1] if len(parts) > 1 else ""

    @property
    def short(self) -> str:
        return f"{self.surname} {self.first_name}".strip()

    @property
    def bitrix_user_id(self) -> int:
        # В демо id пользователя Битрикс24 совпадает с id сотрудника в 1С. В реальном внедрении —
        # таблица соответствия (или поиск через user.search по e-mail).
        return self.id


class EmployeeDirectory:
    def __init__(self, employees: list[Employee]):
        self.employees = employees

    @classmethod
    def from_db(cls, executor) -> EmployeeDirectory:
        rows = executor.fetch("SELECT id, full_name, position, city FROM analytics.managers ORDER BY id")
        return cls([Employee(*r) for r in rows])

    def find(self, surname: str | None, first_name: str | None = None) -> list[Employee]:
        """Поиск по фамилии в именительном падеже (её нормализует модель) и, если есть, по имени."""
        if not surname:
            return []
        s = _norm(surname)
        found = [e for e in self.employees if _norm(e.surname) == s]
        if not found:  # «Калиев» vs «Калиева», опечатки в окончании — сравниваем без последней буквы
            found = [e for e in self.employees if _norm(e.surname).rstrip("а") == s.rstrip("а")]
        if first_name and len(found) > 1:
            f = _norm(first_name)
            narrowed = [e for e in found if _norm(e.first_name).startswith(f[:4])]
            found = narrowed or found
        return found


def _norm(s: str) -> str:
    return s.strip().lower().replace("ё", "е")
