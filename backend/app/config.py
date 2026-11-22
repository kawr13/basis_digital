"""Конфигурация кабинета веб-статистики.

Все значения берутся из окружения (или .env). Ничего секретного в коде.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Известный дефолт: в проде он запрещён — подпись таким ключом подбирается за секунды.
DEFAULT_SECRET_KEY = "dev-only-secret-change-me"
MIN_SECRET_BYTES = 32          # HS256 по RFC 7518 требует не меньше 256 бит


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore",
                                      validate_default=True)

    app_name: str = "Кабинет веб-статистики"
    environment: str = "dev"  # dev | prod

    @field_validator("environment", mode="before")
    @classmethod
    def _environment_known(cls, v):
        # Опечатка ENVIRONMENT=production раньше молча включала dev-режим:
        # COOKIE_SECURE=false, демо-пользователи, открытый Swagger.
        value = (v or "dev").strip().lower()
        if value not in ("dev", "prod"):
            raise ValueError(f"ENVIRONMENT бывает только dev или prod, получено: {v!r}")
        return value

    # База данных. Прод — PostgreSQL, разработка — SQLite из коробки.
    database_url: str = "sqlite:///./data/statcab.db"

    # JWT. В продакшене обязательно сгенерировать: openssl rand -hex 32
    secret_key: str = DEFAULT_SECRET_KEY
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 30
    refresh_token_ttl_days: int = 1

    # Флаг Secure у refresh-cookie. Пусто (auto) — включён, когда ENVIRONMENT=prod.
    # Внутренний сервер без HTTPS: поставь COOKIE_SECURE=false, иначе браузер
    # перестанет присылать cookie и сессия умрёт через access_token_ttl_minutes.
    cookie_secure: bool | None = None

    @field_validator("cookie_secure", mode="before")
    @classmethod
    def _blank_cookie_secure_is_auto(cls, v):
        # COOKIE_SECURE= (пусто) — то же самое, что не задан: авто-режим.
        return None if v is None or (isinstance(v, str) and not v.strip()) else v

    @property
    def cookie_secure_effective(self) -> bool:
        """Явный COOKIE_SECURE важнее правила «prod ⇒ Secure»."""
        return self.environment == "prod" if self.cookie_secure is None else self.cookie_secure

    # Защита от подбора пароля
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    login_throttle_per_ip: int = 20  # попыток в минуту на IP

    # Доверять X-Forwarded-For. По умолчанию нет: заголовок придумывает клиент, а по
    # этому адресу считаются попытки подбора пароля и записи AuditLog. Перед кабинетом
    # стоит наш nginx/Caddy (docker-compose) — включай, иначе все люди сольются в
    # 127.0.0.1 и лимит станет общим на всех.
    trust_proxy_headers: bool = False
    # Сколько своих hops добавляет последний прокси: из X-Forwarded-For берём запись,
    # отступив справа на это число (nginx записывает один адрес).
    trust_proxy_hops: int = 1

    # Фронт, которому разрешён CORS (через запятую)
    cors_origins: str = "http://localhost:5173,http://localhost:8080"

    # Источник данных 1С: mock | http
    one_c_source: str = "mock"
    one_c_base_url: str = ""          # http://1c-server/StatCabinet
    one_c_login: str = ""
    one_c_password: str = ""
    one_c_timeout_sec: int = 30
    # Таймаут «проверки связи» с 1С: он заведомо короче рабочего, чтобы клик
    # в админке не вешал поток кабинета на 4 × one_c_timeout_sec (см. docs/03, §4).
    one_c_probe_timeout_sec: int = 8

    # Пул соединений PostgreSQL. SQLite их не понимает, поэтому db.py передаёт их
    # только не-sqlite URL.
    # Потолок для сервера: воркеров uvicorn × (db_pool_size + db_max_overflow)
    # должно быть меньше max_connections PostgreSQL (по умолчанию 100).
    db_pool_size: int = 10
    db_max_overflow: int = 20
    # Переиспользовать соединение через N секунд: pooler или файрвол молча рвёт
    # простой коннект — recycle дешевле, чем заметить это на первом же запросе.
    db_pool_recycle_sec: int = 1800

    # Токен для push-режима: 1С сама присылает срез на POST /api/onec/push
    # (заголовок X-OneC-Token). Пусто — push выключен (503).
    onec_push_token: str = ""

    # Синхронизация из 1С по расписанию
    sync_enabled: bool = True
    sync_interval_minutes: int = 15

    # Сколько живут служебные записи (п. 14 TASKS2.md). Витрину и план судов кабинет
    # не удаляет никогда: это данные порта. А журнал действий и журнал загрузок
    # растут каждый день и старше нескольких месяцев не нужны никому — без чистки
    # audit_log через год становится самой большой таблицей базы и раздувает бэкап.
    retention_audit_days: int = 365
    retention_runs_days: int = 90

    # Отчёт диспетчера (страница «Статистика»): путь к файлу на сервере и автоимпорт
    dispatcher_xlsm_path: str = ""
    dispatcher_auto_import: bool = True

    # Пароль администратора при первом запуске (если не задан — сгенерируем и печатанём в лог)
    admin_login: str = "admin"
    admin_password: str = ""

    # Наполнять витрину mock-данными только по явному включению: боевой кабинет
    # с позабытым SEED_DEMO_DATA показывал бы фейковые тонны рядом с настоящими.
    seed_demo_data: bool = False

    @model_validator(mode="after")
    def _prod_must_be_hardened(self) -> "Settings":
        """Прод не стартует с небезопасной конфигурацией: падение лучше тихой дыры."""
        if self.environment != "prod":
            return self
        key = self.secret_key or ""
        if not key.strip() or key == DEFAULT_SECRET_KEY or len(key.encode()) < MIN_SECRET_BYTES:
            raise ValueError(
                "SECRET_KEY в prod обязателен, не должен совпадать с демо-значением и должен "
                f"быть не короче {MIN_SECRET_BYTES} байт (openssl rand -hex 32)")
        if self.admin_password and len(self.admin_password) < 8:
            raise ValueError("ADMIN_PASSWORD в prod — не короче 8 символов")
        if "*" in self.cors_origin_list:
            raise ValueError("CORS_ORIGINS не может содержать * при allow_credentials=true")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
