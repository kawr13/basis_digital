"""Настройки времени выполнения, которые администратор меняет прямо в кабинете.

Зачем это нужно: часть параметров (интервал синхронизации, пороги загрузки,
лимит неудачных входов, адрес 1С) админу нужно подкручивать без релиза и без
доступа к серверу. При этом существующий `.env` обязан продолжать работать:

    приоритет значения: запись в БД (app_settings) → значение из .env → DEFAULTS

Пока админ ничего не трогал в UI, кабинет ведёт себя ровно как раньше.

Здесь же — валидация (тип, диапазон, согласованность пары порогов) и аудит:
любая запись настроек оставляет след в `audit_log`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import AppSetting, AuditLog, User, utcnow

# Чем заменяем секрет в ответах наружу. Если админ пришлёт это обратно —
# считаем, что секрет не меняли (UI показывает только маску).
MASK = "***"

# Секретные ключи: наружу не отдаём никогда, только флаг «задан».
SECRET_KEYS = ("onec.password", "onec.push_token")


@dataclass(frozen=True)
class Spec:
    """Описание одной настройки: тип, диапазон, откуда брать значение из .env."""

    kind: str                                   # str | int | float | bool | choice
    default: Any
    env: str | None = None                      # атрибут Settings ("" = из .env не берём)
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] | None = None
    secret: bool = False
    label: str = ""
    hint: str = ""


SPEC: dict[str, Spec] = {
    # --- интеграция с 1С -----------------------------------------------------
    "onec.source": Spec("choice", "mock", env="one_c_source", choices=("mock", "http"),
                        label="Источник данных 1С", hint="mock = демо-данные, http = HTTP-сервис 1С"),
    "onec.base_url": Spec("str", "", env="one_c_base_url", label="Адрес HTTP-сервиса 1С",
                          hint="например http://1c-server/StatCabinet"),
    "onec.login": Spec("str", "", env="one_c_login", label="Логин чтения для 1С"),
    "onec.password": Spec("str", "", env="one_c_password", secret=True, label="Пароль для 1С"),
    "onec.push_token": Spec("str", "", env="onec_push_token", secret=True,
                            label="Токен push из 1С",
                            hint="заголовок X-OneC-Token; пусто — push выключен"),
    "onec.timeout_sec": Spec("int", 30, env="one_c_timeout_sec", minimum=5, maximum=300,
                             label="Таймаут запросов к 1С, сек"),
    # --- синхронизация -------------------------------------------------------
    "sync.enabled": Spec("bool", True, env="sync_enabled", label="Синхронизировать по расписанию"),
    "sync.interval_minutes": Spec("int", 15, env="sync_interval_minutes", minimum=1, maximum=1440,
                                  label="Интервал синхронизации, мин"),
    "sync.window_days": Spec("int", 30, minimum=7, maximum=366,
                             label="За какой период тянуть динамику, суток"),
    # --- что видит пользователь ---------------------------------------------
    "ui.dynamics_days": Spec("int", 14, minimum=7, maximum=90,
                             label="Динамика на дашборде, суток"),
    "reports.default_days": Spec("int", 30, minimum=7, maximum=366,
                                 label="Период отчёта по умолчанию, суток"),
    "ui.refresh_seconds": Spec("int", 60, minimum=5, maximum=900,
                               label="Как часто фронт сам подтягивает данные, сек",
                               hint="только когда вкладка видна; 15 с — интервал опроса свежести"),
    "thresholds.capacity_warn": Spec("int", 85, minimum=50, maximum=100,
                                     label="Порог «внимание», % загрузки склада"),
    "thresholds.capacity_crit": Spec("int", 95, minimum=50, maximum=100,
                                     label="Порог «критично», % загрузки склада"),
    # --- доступ к «Постановке судов» ----------------------------------------
    # Роли через запятую. Раздельно: просмотр / постановка судна / удаление.
    # Смысл: план ведёт диспетчер, но остальной кабинет он — менеджер.
    "vessels.view_roles": Spec("str", "admin,top,manager",
                                label="Кто видит постановку судов",
                                hint="роли через запятую: admin, top, manager"),
    "vessels.edit_roles": Spec("str", "admin,top",
                               label="Кто ставит и правит суда",
                               hint="роли через запятую: admin, top, manager"),
    "vessels.delete_roles": Spec("str", "admin",
                                 label="Кто удаляет постановки",
                                 hint="роли через запятую: admin, top, manager"),
    # --- безопасность входа --------------------------------------------------
    "security.max_failed_logins": Spec("int", 5, env="max_failed_logins", minimum=3, maximum=20,
                                       label="Неудачных входов до блокировки"),
    "security.lockout_minutes": Spec("int", 15, env="lockout_minutes", minimum=1, maximum=1440,
                                     label="На сколько минут блокировать учётку"),
}

# Публичный набор: любой авторизованный пользователь (менеджеру — пороги и
# периоды, чтобы UI и бэкенд считали одинаково).
PUBLIC_KEYS = ("ui.dynamics_days", "reports.default_days", "ui.refresh_seconds",
               "thresholds.capacity_warn", "thresholds.capacity_crit",
               "vessels.view_roles", "vessels.edit_roles", "vessels.delete_roles")

# Допустимые роли для настроек доступа к постановке судов.
ROLE_TOKENS = ("admin", "top", "manager")

DEFAULTS: dict[str, Any] = {key: spec.default for key, spec in SPEC.items()}

_MAX_STR_LEN = 500


# --------------------------------------------------------------------- чтение ----

def _decode(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def db_values(db: Session) -> dict[str, Any]:
    """Значения, которые администратор сохранил в БД (без .env и DEFAULTS)."""
    out: dict[str, Any] = {}
    for rec in db.scalars(select(AppSetting)):
        if rec.key in SPEC:
            out[rec.key] = _decode(rec.value)
    return out


def overridden(db: Session) -> list[str]:
    """Ключи, перекрывающие .env (то, что админ реально менял)."""
    return sorted(db_values(db).keys())


def _env_value(spec: Spec) -> Any:
    if not spec.env:
        return None
    value = getattr(get_settings(), spec.env, None)
    if value is None:
        return None
    if spec.kind == "choice":
        return str(value).strip().lower()
    if spec.kind == "int":
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if spec.kind == "float":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if spec.kind == "bool":
        return bool(value)
    return str(value)


def get(db: Session, key: str) -> Any:
    """Эффективное значение настройки: БД → .env → DEFAULTS."""
    stored = db.scalar(select(AppSetting).where(AppSetting.key == key))
    if stored is not None:
        return _decode(stored.value)
    env_value = _env_value(SPEC[key])
    return DEFAULTS[key] if env_value is None else env_value


def get_all(db: Session) -> dict[str, Any]:
    """Все эффективные значения одним заходом (включая секреты — только для кода)."""
    stored = db_values(db)
    out: dict[str, Any] = {}
    for key, spec in SPEC.items():
        if key in stored:
            out[key] = stored[key]
            continue
        env_value = _env_value(spec)
        out[key] = DEFAULTS[key] if env_value is None else env_value
    return out


def masked(db: Session) -> dict[str, Any]:
    """Настройки для админского UI: секреты наружу не отдаём."""
    values = get_all(db)
    for key in SECRET_KEYS:
        values[f"{key}_set"] = bool(values.get(key))
        values[key] = MASK if values.get(key) else ""
    return values


def public(db: Session) -> dict[str, Any]:
    """Безопасный набор для любой авторизованной роли."""
    values = get_all(db)
    return {key: values[key] for key in PUBLIC_KEYS}


def specs() -> dict[str, Any]:
    """Метаданные для формы админки: типы, диапазоны, подсказки."""
    return {
        key: {
            "kind": spec.kind,
            "min": spec.minimum,
            "max": spec.maximum,
            "choices": list(spec.choices) if spec.choices else None,
            "secret": spec.secret,
            "label": spec.label or key,
            "hint": spec.hint,
            "from_env": spec.env or None,
        }
        for key, spec in SPEC.items()
    }


# ------------------------------------------------------------------- валидация ----

def _coerce(key: str, spec: Spec, value: Any) -> Any:
    """Приводим присланное значение к типу настройки; при ошибке — ValueError по-русски."""
    label = spec.label or key

    if spec.kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "false", "1", "0", "да", "нет"}:
            return value.strip().lower() in {"true", "1", "да"}
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        raise ValueError(f"«{label}»: нужно да или нет")

    if spec.kind in ("int", "float"):
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError(f"«{label}»: нужно число")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"«{label}»: нужно число")
        if spec.kind == "int":
            if number != int(number):
                raise ValueError(f"«{label}»: нужно целое число")
            number = int(number)
        if spec.minimum is not None and number < spec.minimum:
            raise ValueError(f"«{label}»: не меньше {spec.minimum}")
        if spec.maximum is not None and number > spec.maximum:
            raise ValueError(f"«{label}»: не больше {spec.maximum}")
        return number

    if spec.kind == "choice":
        if not isinstance(value, str):
            raise ValueError(f"«{label}»: строка")
        choice = value.strip().lower()
        if choice not in (spec.choices or ()):
            raise ValueError(f"«{label}»: допустимо только {', '.join(spec.choices or ())}")
        return choice

    # str
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"«{label}»: строка")
    value = value.strip()
    if len(value) > _MAX_STR_LEN:
        raise ValueError(f"«{label}»: не длиннее {_MAX_STR_LEN} символов")
    if key.endswith("_roles"):
        tokens = [t.strip().lower() for t in value.split(",") if t.strip()]
        if not tokens:
            raise ValueError(f"«{label}»: укажите хотя бы одну роль")
        bad = [t for t in tokens if t not in ROLE_TOKENS]
        if bad:
            raise ValueError(f"«{label}»: роли не такие: {', '.join(bad)}. Можно: admin, top, manager")
        # нормализуем: без дублей и пробелов, в алфавитном порядке
        return ",".join(sorted(dict.fromkeys(tokens)))
    return value


def role_set(db: Session, key: str) -> set[str]:
    """Роли, которым разрешено действие (настройка вида `vessels.*_roles`)."""
    raw = get_all(db).get(key) or ""
    return {t.strip().lower() for t in str(raw).split(",") if t.strip()}


def _validate_together(values: dict[str, Any]) -> None:
    """Согласованность пары порогов: «критично» не ниже «внимания»."""
    warn = values.get("thresholds.capacity_warn")
    crit = values.get("thresholds.capacity_crit")
    if warn is not None and crit is not None and crit < warn:
        raise ValueError("Порог «критично» не может быть ниже порога «внимание»")


# --------------------------------------------------------------------- запись ----

def apply(db: Session, patch: dict[str, Any], actor: User | None = None) -> dict[str, Any]:
    """Сохранить частичный набор настроек. Возвращает все эффективные значения.

    Пишем только те ключи, значение которых действительно изменилось.
    Ошибки — ValueError с русским текстом (эндпоинт превращает их в 422).
    """
    if not isinstance(patch, dict):
        raise ValueError("Ожидался набор настроек")
    if not patch:
        raise ValueError("Пустой набор настроек")

    # UI возвращает и служебные флаги onec.password_set / onec.push_token_set —
    # это не настройки, молча пропускаем.
    real = {k: v for k, v in patch.items() if not (k.endswith("_set") and k[:-4] in SPEC)}
    unknown = sorted(k for k in real if k not in SPEC)
    if unknown:
        raise ValueError("Неизвестные настройки: " + ", ".join(unknown))

    changed: dict[str, Any] = {}
    current = get_all(db)
    for key, raw in real.items():
        spec = SPEC[key]
        if spec.secret and isinstance(raw, str) and raw.strip() == MASK:
            continue                      # UI вернул маску — секрет не меняем
        value = _coerce(key, spec, raw)
        if value == current.get(key):
            continue                      # уже так — не плодим запись в БД
        changed[key] = value

    if not changed:
        return current

    merged = {**current, **changed}
    _validate_together(merged)

    for key, value in changed.items():
        rec = db.scalar(select(AppSetting).where(AppSetting.key == key))
        payload = json.dumps(value, ensure_ascii=False)
        if rec:
            rec.value = payload
            rec.updated_at = utcnow()
            rec.updated_by = actor.id if actor else None
        else:
            db.add(AppSetting(key=key, value=payload,
                              updated_by=actor.id if actor else None))

    db.add(AuditLog(user_id=actor.id if actor else None, login=actor.login if actor else None,
                    action="admin.settings_update",
                    detail="ключи: " + ", ".join(sorted(changed))))
    db.commit()
    return get_all(db)


def reset(db: Session, keys: list[str], actor: User | None = None) -> dict[str, Any]:
    """Сбросить ключи к значениям из .env/DEFAULTS (удалить запись из БД)."""
    removed: list[str] = []
    for key in keys:
        if key not in SPEC:
            raise ValueError(f"Неизвестная настройка: {key}")
        rec = db.scalar(select(AppSetting).where(AppSetting.key == key))
        if rec:
            db.delete(rec)
            removed.append(key)
    if removed:
        db.add(AuditLog(user_id=actor.id if actor else None, login=actor.login if actor else None,
                        action="admin.settings_reset", detail="ключи: " + ", ".join(sorted(removed))))
        db.commit()
    return get_all(db)
