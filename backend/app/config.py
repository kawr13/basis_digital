"""Конфигурация кабинета веб-статистики.

Все значения берутся из окружения (или .env). Ничего секретного в коде.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Кабинет веб-статистики"
    environment: str = "dev"  # dev | prod

    # База данных. Прод — PostgreSQL, разработка — SQLite из коробки.
    database_url: str = "sqlite:///./data/statcab.db"

    # JWT. В продакшене обязательно сгенерировать: openssl rand -hex 32
    secret_key: str = "dev-only-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 30
    refresh_token_ttl_days: int = 1

    # Защита от подбора пароля
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    login_throttle_per_ip: int = 20  # попыток в минуту на IP

    # Фронт, которому разрешён CORS (через запятую)
    cors_origins: str = "http://localhost:5173,http://localhost:8080"

    # Источник данных 1С: mock | http
    one_c_source: str = "mock"
    one_c_base_url: str = ""          # http://1c-server/StatCabinet
    one_c_login: str = ""
    one_c_password: str = ""
    one_c_timeout_sec: int = 30

    # Токен для push-режима: 1С сама присылает срез на POST /api/onec/push
    # (заголовок X-OneC-Token). Пусто — push выключен (503).
    onec_push_token: str = ""

    # Синхронизация из 1С по расписанию
    sync_enabled: bool = True
    sync_interval_minutes: int = 15

    # Пароль администратора при первом запуске (если не задан — сгенерируем и печатанём в лог)
    admin_login: str = "admin"
    admin_password: str = ""

    seed_demo_data: bool = True       # наполнить витрину демо-данными, пока 1С не подключена

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
