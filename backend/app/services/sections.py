"""Какие разделы кабинета («вкладки») открыты человеку.

Раздел один на весь кабинет: его id (`statistics`, `warehouses`, `reports`,
`vessels`) фигурирует и в глобальной настройке `ui.hidden_sections`, и в личном
списке `users.sections`, и в кладках шапки. Правило пересечения здесь одно, и
каждое следующее правило только сужает доступ — расширять не может никакое:

    1. `ui.hidden_sections` — раздел скрыт для всех. Личные галки не помогают:
       админ, спрятавший раздел у всего кабинета, не ждёт, что кто-то обойдёт
       это личной вкладкой;
    2. `users.sections` — личный список вкладок человека (NULL = личных
       ограничений нет, тогда пункт просто пропущен);
    3. правила по роли — для «Постановки судов» это `vessels.view_roles` и
       личное `users.vessel_right` (`services/vessels.py::rights_for`),
       остальные разделы открыты любой авторизованной роли.

Здесь же разбор и валидация личного списка: колонка хранит id через запятую,
а наружу (pydantic и фронт) список уходит массивом. Подписи разделов тоже одни
на весь бэкенд — чтобы деталь 403 и галки в админке называли вкладку одинаково.
"""
from __future__ import annotations

from typing import Iterable

from sqlalchemy.orm import Session

from . import app_settings
from ..models import User

# Порядок этого кортежа — порядок вкладок в шапке и в значении колонки.
SECTION_IDS: tuple[str, ...] = ("statistics", "warehouses", "reports", "vessels")

SECTION_LABELS: dict[str, str] = {
    "statistics": "Индикаторы",
    "warehouses": "Склады 1С",
    "reports": "Отчёты",
    "vessels": "Постановка судов",
}

# Глобальная настройка «скрыть для всех» (её choices и есть допустимые id).
HIDDEN_KEY = "ui.hidden_sections"


class BadSections(ValueError):
    """Список вкладок из админки не подходит: неизвестный id или список пуст."""


def label(section: str) -> str:
    return SECTION_LABELS.get(section, section)


# ------------------------------------------------------------------ колонка ----

def parse(raw: str | None) -> list[str] | None:
    """Значение `users.sections` → список id. Пусто/NULL = личных ограничений нет."""
    if not raw or not str(raw).strip():
        return None
    known = {t.strip() for t in str(raw).split(",") if t.strip()}
    # порядок — из SECTION_IDS, а не из строки: так значение не зависит от того,
    # в каком порядке админ ставил галки
    return [s for s in SECTION_IDS if s in known]


def normalize(values: Iterable[str] | None) -> str | None:
    """Список из админки → значение колонки: только известные id, без дублей.

    `None` — снять личные ограничения (человек снова наследует роль). Пустой
    список запрещён: учётка без вкладок не покажет ни одну страницу кабинета, а
    возвращать её обратно придётся через админку.
    """
    if values is None:
        return None
    if isinstance(values, str):
        raise BadSections("Вкладки — это список идентификаторов разделов")
    picked: set[str] = set()
    for raw in values:
        token = str(raw).strip().lower()
        if token not in SECTION_LABELS:
            raise BadSections(
                f"Неизвестная вкладка: {raw}. Можно: {', '.join(SECTION_IDS)}")
        picked.add(token)
    if not picked:
        raise BadSections(
            "Нельзя оставить пользователя без вкладок: отметьте хотя бы одну")
    return ",".join(s for s in SECTION_IDS if s in picked)


def personal(user: User | None) -> set[str] | None:
    """Личный список вкладок человека; `None` — личных ограничений нет."""
    if user is None:
        return None
    own = parse(user.sections)
    return None if own is None else set(own)


# ------------------------------------------------------------------- скрытость --

def hidden_ids(db: Session) -> set[str]:
    """Разделы, скрытые во всём кабинете (`ui.hidden_sections`)."""
    raw = app_settings.get(db, HIDDEN_KEY) or []
    if isinstance(raw, str):
        raw = [t.strip() for t in raw.split(",") if t.strip()]
    return {str(t) for t in raw}


def section_visible(db: Session, user: User | None, section: str) -> bool:
    """Не скрыт ли раздел (глобальная галка + личный список), правил роли касается отдельно."""
    if section in hidden_ids(db):
        return False
    own = personal(user)
    return own is None or section in own


def section_allowed(db: Session, user: User | None, section: str) -> bool:
    """Открыт ли раздел человеку целиком: скрытость, затем правила роли."""
    if not section_visible(db, user, section):
        return False
    if section == "vessels":
        # локальный импорт: services/vessels.py живёт рядом и уже импортирует
        # app_settings — встречный импорт на уровне модуля дал бы цикл
        from .vessels import rights_for
        return bool(rights_for(db, user).get("view"))
    return True


def deny(db: Session, user: User | None, section: str) -> str:
    """Причина отказа по-русски: «скрыт для всех» и «вам не назначен» — разные."""
    if section in hidden_ids(db):
        return f"Раздел «{label(section)}» скрыт для всех в настройках кабинета"
    return f"Раздел «{label(section)}» вам не назначен (личные вкладки пользователя)"
