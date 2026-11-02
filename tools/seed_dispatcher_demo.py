"""Загрузчик демо-отчёта диспетчера в работающий кабинет.

Зачем. Витрину «Статистики» наполняет только импорт «Отчёта диспетчера»:
`POST /api/admin/dispatcher/import` → `dispatcher_xlsm.parse_workbook` →
`dispatcher.apply_snapshot`. Чтобы демо не выглядело вставленным в таблицы,
файл собирает `app.onec.dispatcher_demo` и грузит через тот же HTTP-импорт:
путь источника, `source` строк, границы витрины, инвалидация кеша и ETag
ведут себя как с настоящим отчётом.

Почему через API, а не в базу. Бэкенд держит витрину в своей базе (SQLite или
PostgreSQL — каким указал `DATABASE_URL`), и только импорт гарантирует, что
данные легли в ту же базу, что читает кабинет, и что кэш ответов протух в тот
же момент. Исключение — `--purge`: эндпоинта «удалить строки витрины» в кабинете
нет (и не должно быть: это не пользовательская операция), поэтому снятие демо
идёт напрямую в БД — по тому же `DATABASE_URL`, что у бэкенда (берём из
`backend/.env`, если он не задан в окружении).

Запуск (из корня репозитория, бэкенд должен работать):
    API=http://127.0.0.1:8000 LOGIN=admin PASS='***' \\
        backend/.venv/Scripts/python.exe tools/seed_dispatcher_demo.py
    ... tools/seed_dispatcher_demo.py --purge        # снять демо
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))


def _backend_database_url() -> None:
    """Подставить DATABASE_URL из `backend/.env`, если он не задан в окружении.

    Порядок вызова важен: приложение читает .env из ТЕКУЩЕЙ папки, а скрипт
    запускают из корня репозитория, а `engine` создаётся на импорте `app.db` —
    позже переписать базу уже нельзя. Без этого шага `--purge` чистил бы пустую
    `./data/statcab.db` вместо базы, на которой реально работает бэкенд.
    """
    env_file = BACKEND / ".env"
    if not env_file.exists() or os.environ.get("DATABASE_URL"):
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "DATABASE_URL" and value.strip():
            os.environ["DATABASE_URL"] = value.strip()
            return


_backend_database_url()        # ДО импорта `app.*` — см. докстринг функции

from app.onec.dispatcher_demo import AHEAD_DAYS, build_report      # noqa: E402

DEFAULT_OUT = BACKEND / "data" / "demo_report.xlsm"
DEFAULT_DAYS = 45           # ~45 суток назад: страница показывает 30, есть с запасом
SOURCE_MARKER = "xlsm"      # что пишет `apply_snapshot` в колонку `source`


# --------------------------------------------------------------------- http ----

class Api:
    """Минимальный клиент кабинета: логин + JSON поверх urllib (без новых зависимостей)."""

    def __init__(self, base: str, login: str, password: str, timeout: int = 180) -> None:
        self.base = base.rstrip("/")
        self.login = login
        self.password = password
        self.timeout = timeout
        self.token = ""

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(f"{self.base}{path}", data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:            # 4xx/422 — текст ошибки даёт бэк
            detail = exc.read().decode("utf-8", "replace")
            try:
                detail = json.loads(detail).get("detail", detail)
            except json.JSONDecodeError:
                pass
            raise SystemExit(f"{method} {path} → {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise SystemExit(f"{self.base} недоступен: {exc}. Бэкенд запущен? API=... задан?") from exc
        return json.loads(raw or b"{}")

    def authenticate(self) -> None:
        self.token = self.call("POST", "/api/auth/login",
                               {"login": self.login, "password": self.password})["access_token"]


# ------------------------------------------------------------------- утилиты ----



def _human(size: int) -> str:
    """Размер файла по-человечески: 336049 → «328.2 КБ»."""
    value = float(size)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            return f"{value:.0f} {unit}" if unit == "Б" else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} ГБ"


def _period(args) -> tuple[date, date]:
    end = date.fromisoformat(args.end) if args.end else date.today()
    return end - timedelta(days=args.days - 1), end


# -------------------------------------------------------------------- seed ----

def seed(api: Api, args) -> int:
    out = Path(args.out).resolve()
    start, end = _period(args)
    report = build_report(days=args.days, end=end, seed=args.seed)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(report)
    print(f"файл   : {out} ({_human(len(report))}), seed={args.seed}")
    print(f"период : {start} .. {end} ({args.days} суток), "
          f"«на подходе» ещё {AHEAD_DAYS} суток")

    result = api.call("POST", "/api/admin/dispatcher/import", {
        "content_base64": base64.b64encode(report).decode(), "filename": out.name})
    print(result["described"])                       # ровно `describe(snapshot)`
    imported, total = result["imported"], result["total"]
    print("загружено: " + ", ".join(f"{k}={v}" for k, v in imported.items() if v))
    print("в витрине: " + ", ".join(f"{k}={v}" for k, v in total.items()))

    if not args.no_setting:
        status = api.call("GET", "/api/admin/dispatcher/status")
        before = status.get("configured_path") or "—"
        api.call("PUT", "/api/admin/settings", {"dispatcher.source_path": str(out)})
        after = (api.call("GET", "/api/admin/dispatcher/status").get("configured_path")
                 or "—")
        print(f"dispatcher.source_path: {before} → {after}")
        if not args.no_autoimport:
            api.call("PUT", "/api/admin/settings", {"dispatcher.auto_import": True})
    print("готово: страница «Статистика» должна показать смены, суда и плановые заходы")
    return 0


# ------------------------------------------------------------------- purge ----

def _purge_rows(start: date, end: date) -> dict:
    """Удалить строки витрины за период, которые положил импорт.

    Фильтр по `source == 'xlsm'` — не косметика: демо пишет именно этот
    источник (`apply_snapshot` берёт его из снимка), а в базе за тот же период
    могут лежать строки, загруженные другим путём. Полностью период чистить
    нельзя — снесли бы чужие данные.

    Эндпоинта «удалить строки витрины» в кабинете нет (и не должно быть: это не
    пользовательская операция), поэтому снятие демо идёт напрямую в ту же базу,
    что читает бэкенд.
    """
    from app.config import get_settings
    from app.db import SessionLocal
    from app.models import (
        FactDispatcherShift, FactPlatformBalance, FactRailArrival, FactRailDelivery,
        FactRailDeparture, FactRailOperation, FactTruckOperation,
        FactVesselArrival, FactVesselOperation,
    )
    from sqlalchemy import delete
    from sqlalchemy.exc import SQLAlchemyError

    url = get_settings().database_url
    print(f"база   : {url}")
    deleted: dict[str, int] = {}
    db = SessionLocal()
    try:
        for model in (FactDispatcherShift, FactVesselOperation, FactRailOperation,
                      FactTruckOperation, FactVesselArrival, FactRailArrival,
                      FactRailDeparture, FactRailDelivery, FactPlatformBalance):
            stmt = delete(model).where(model.report_date >= start,          # type: ignore[attr-defined]
                                       model.report_date <= end,            # type: ignore[attr-defined]
                                       model.source == SOURCE_MARKER)       # type: ignore[attr-defined]
            deleted[model.__tablename__] = int(db.execute(stmt).rowcount or 0)
        db.commit()
    except SQLAlchemyError as exc:
        raise SystemExit(f"не удалось почистить {url}: {exc}. Совпадает ли DATABASE_URL "
                         "с базой бэкенда?") from exc
    finally:
        db.close()
    return deleted


def purge(api: Api, args) -> int:
    out = Path(args.out).resolve()
    start, end = _period(args)
    status = api.call("GET", "/api/admin/dispatcher/status")
    configured = str(status.get("configured_path") or "")

    print(f"период : {start} .. {end} ({args.days} суток)")
    if configured and os.path.normcase(Path(configured).resolve()) == os.path.normcase(out):
        api.call("PUT", "/api/admin/settings", {"dispatcher.source_path": ""})
        print(f"dispatcher.source_path: {configured} → снят")
    elif configured:
        print(f"dispatcher.source_path не тронут: он про {configured}")

    deleted = _purge_rows(start, end)
    summary = ", ".join(f"{k}={v}" for k, v in deleted.items() if v)
    print(f"удалено: {summary or 'ничего'}")

    if out.exists() and not args.keep_file:
        out.unlink()
        print(f"файл удалён: {out}")
    counts = api.call("GET", "/api/admin/dispatcher/status")["counts"]
    print("в витрине: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    print("кэш витрины обновится сам (версия данных меняется вместе со строками)")
    return 0


# --------------------------------------------------------------------- cli ----

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Собрать демо-«Отчет диспетчера» и загрузить его настоящим импортом.")
    parser.add_argument("--api", default=os.environ.get("API", "http://127.0.0.1:8000"),
                        help="адрес бэкенда (окружение API)")
    parser.add_argument("--login", default=os.environ.get("LOGIN", "admin"),
                        help="логин администратора (окружение LOGIN)")
    parser.add_argument("--password", default=os.environ.get("PASS", ""),
                        help="пароль администратора (окружение PASS)")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help=f"куда положить файл (по умолчанию {DEFAULT_OUT})")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help=f"сколько суток покрыть (по умолчанию {DEFAULT_DAYS})")
    parser.add_argument("--end", default=None, help="последняя дата отчёта, ГГГГ-ММ-ДД (сегодня)")
    parser.add_argument("--seed", type=int, default=42, help="зерно генератора (файл воспроизводим)")
    parser.add_argument("--no-setting", action="store_true",
                        help="не менять dispatcher.source_path")
    parser.add_argument("--no-autoimport", action="store_true",
                        help="не включать dispatcher.auto_import")
    parser.add_argument("--purge", action="store_true",
                        help="снять демо: удалить строки за период и сбросить путь источника")
    parser.add_argument("--keep-file", action="store_true",
                        help="с --purge: не удалять сам файл")
    args = parser.parse_args(argv)

    if not args.password:
        parser.error("нужен пароль: PASS=... или --password")

    api = Api(args.api, args.login, args.password)
    api.authenticate()
    return purge(api, args) if args.purge else seed(api, args)


if __name__ == "__main__":
    sys.exit(main())
