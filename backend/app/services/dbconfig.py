"""База данных кабинета: части URL, проверка кандидата, переезд без рестарта.

Зачем (п. 26–28 TASKS2.md). Раньше перенести кабинет на другую базу мог только
человек с доступом к серверу: поправить `.env`, пересобрать/перезапустить
контейнер. Админ должен сделать то же из браузера — и сначала убедиться, что в
новую базу вообще можно подключиться, не ломая текущую работу.

Как уживается с `.env`. Точкой отката остаётся `DATABASE_URL` окружения:

    db.use_settings = false → работает `DATABASE_URL` (как до появления вкладки)
    db.use_settings = true  → работает база, собранная из частей ниже

Кнопка «вернуть ENV» выключает флаг, и кабинет снова слушает окружение. Сами
части при этом не стираются: админ может поправить адрес и включить обратно.

Драйвер. Схему берём из `DATABASE_URL`, если это семейство PostgreSQL (в бою
`postgresql+psycopg2://` — именно `psycopg2-binary` лежит в
`requirements-prod.txt`); иначе `postgresql+psycopg2`. Голую схему `postgresql://`
не отдаём: драйвер из неё SQLAlchemy выберет сама, а нам важно, какой именно.

Пароль. Наружу не отдаётся никогда (`app_settings.SECRET_KEYS`), в URL
вставляется URL-кодированным: `@`, `:`, `/` и `%` в паролях — обычное дело, и без
кодирования такой пароль разламывает URL на чужой хост.
"""
from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from ..config import get_settings
from ..db import (SessionLocal, EngineSwapError, current_engine_url, masked_url,
                  rebuild_engine, server_version)
from ..models import User
from ..services import app_settings
from .app_settings import MASK

log = logging.getLogger("statcab.dbconfig")

# Ключи стора, из которых состоит база. `password` — секрет.
KEY_DIALECT = "db.dialect"
KEY_HOST = "db.host"
KEY_PORT = "db.port"
KEY_USER = "db.user"
KEY_PASSWORD = "db.password"
KEY_DATABASE = "db.database"
KEY_ENABLED = "db.use_settings"

PART_KEYS = (KEY_DIALECT, KEY_HOST, KEY_PORT, KEY_USER, KEY_PASSWORD, KEY_DATABASE)

# Источник эффективности базы: части из настроек либо `DATABASE_URL` окружения.
SOURCE_SETTINGS = "settings"
SOURCE_ENV = "env"

# Сколько ждать ответа от кандидата. Проверку делают из браузера админа, поэтому
# «вечерить» она не имеет права: висящий порт виден через секунды, а не через
# таймаут TCP ядра (минуты).
TEST_TIMEOUT_SEC = 8

DEFAULT_PG_SCHEME = "postgresql+psycopg2"


# --------------------------------------------------------------------- части ----

def env_url() -> str:
    """Bootstrap-URL из окружения — то, куда возвращает «вернуть ENV»."""
    return get_settings().database_url


def _scheme() -> str:
    """Схема PostgreSQL-URL: повторяем драйвер окружения, если оно про PostgreSQL."""
    url = env_url()
    if url.startswith("postgresql"):
        return url.split("://", 1)[0]
    return DEFAULT_PG_SCHEME


def parts(db: Session) -> dict[str, Any]:
    """Части базы из стора настроек. Пароля здесь нет — только флаг «задан»."""
    values: dict[str, Any] = {key.split(".", 1)[1]: app_settings.get(db, key) for key in PART_KEYS}
    values["password"] = ""
    return values


def password_set(db: Session) -> bool:
    return bool(app_settings.get(db, KEY_PASSWORD))


def pointer(db: Session) -> bool:
    """Указывает ли ЭТА база на другую (флаг `db.use_settings`).

    Флаг живёт в bootstrap-базе — именно её кабинет читает первой после старта,
    поэтому «продолжить на другой базе» хранится там, а не в той, на которую
    уже переехали.
    """
    return bool(app_settings.get(db, KEY_ENABLED))


def source(db: Session) -> str:
    """Где кабинет живёт сейчас: работающая база против `DATABASE_URL` окружения.

    Спрашивать флаг у текущей базы нельзя: после переезда текущая база — новая,
    а указатель остался в прежней. Поэтому источник определяется фактом: та же
    ли база под процессом, что дало окружение.
    """
    try:
        same = current_engine_url() == make_url(env_url())
    except Exception:  # noqa: BLE001 — DATABASE_URL мог быть любым
        same = False
    return SOURCE_ENV if same else SOURCE_SETTINGS


def env_parts() -> dict[str, Any]:
    """`DATABASE_URL` окружения в тех же частях — чтобы админ видел, куда откатится.

    Пароль не показываем нигде: наружу идёт только маска URL.
    """
    url = make_url(env_url())
    sqlite = url.drivername.startswith("sqlite")
    return {
        "dialect": "sqlite" if sqlite else "postgresql",
        "host": url.host or "",
        "port": int(url.port or (0 if sqlite else 5432)),
        "user": url.username or "",
        "password": "",
        "database": url.database or "",
    }


def build_url(candidate: dict[str, Any]) -> str:
    """Собрать SQLAlchemy-URL из частей. Ошибки — ValueError по-русски (в 422)."""
    dialect = str(candidate.get("dialect") or "postgresql").strip().lower()
    database = str(candidate.get("database") or "").strip()

    if dialect == "sqlite":
        if not database:
            raise ValueError("Для sqlite нужно указать путь к файлу базы")
        # Обратный слэш Windows в URL не живёт; абсолютный путь POSIX и так
        # начинается со «/», поэтому «sqlite:///» + путь даёт нужные 3 или 4 слэша.
        return f"sqlite:///{database.replace(chr(92), '/')}"
    if dialect != "postgresql":
        raise ValueError(f"Неизвестный диалект базы: {dialect}")

    host = str(candidate.get("host") or "").strip()
    user = str(candidate.get("user") or "").strip()
    password = str(candidate.get("password") or "")
    raw_port = candidate.get("port")
    try:
        # `or 5432` не годится: порт 0 — это не «пропустили», а ошибка ввода.
        port = 5432 if raw_port in (None, "") else int(raw_port)
    except (TypeError, ValueError) as exc:
        raise ValueError("Порт базы должен быть числом") from exc
    if not host:
        raise ValueError("Не указан хост сервера PostgreSQL")
    if not user:
        raise ValueError("Не указан пользователь базы")
    if not database:
        raise ValueError("Не указано имя базы")
    if not 1 <= port <= 65535:
        raise ValueError("Порт базы должен быть в диапазоне 1…65535")

    auth = quote(user, safe="")
    if password:
        auth = f"{auth}:{quote(password, safe='')}"
    return f"{_scheme()}://{auth}@{host}:{port}/{quote(database, safe='')}"


def masked(candidate: dict[str, Any]) -> str:
    """URL кандидата для людей и для аудита: пароль заменён на точки.

    Вид — тот же, что у `display_url`: путь sqlite остаётся путём, а не набором
    `%3A`; админ читает этот текст и в ответе проверки, и в журнале.
    """
    try:
        return _human(make_url(build_url(candidate)))
    except ValueError:
        # Конфигурация ещё не полная — показываем то, что есть, без пароля.
        return str(candidate.get("database") or "") or "—"


def _human(url) -> str:
    """URL для людей: sqlite отдаём путём как есть, в PostgreSQL маскируем пароль."""
    if url.drivername.startswith("sqlite"):
        return f"sqlite:///{url.database or ''}"
    return url.render_as_string(hide_password=True)


def display_url() -> str:
    """Работающая база так, чтобы её мог прочитать человек, а не драйвер."""
    return _human(current_engine_url())


def display_env_url() -> str:
    """`DATABASE_URL` окружения в том же виде — «куда вернёт вернуть ENV»."""
    return _human(make_url(env_url()))


def with_password(db: Session, candidate: dict[str, Any]) -> dict[str, Any]:
    """Подставить сохранённый пароль, если UI прислал маску или пустое поле.

    Форма админки не знает пароля (его наружу не отдают), поэтому «не меняли»
    выглядит как маска — её и надо разобрать в прежнее значение.
    """
    sent = candidate.get("password")
    if sent is None or (isinstance(sent, str) and sent.strip() in ("", MASK)):
        return {**candidate, "password": app_settings.get(db, KEY_PASSWORD) or ""}
    return dict(candidate)


# ---------------------------------------------------------------- создание базы ----
# PostgreSQL не позволяет подключиться к базе, которой нет, поэтому «создать базу,
# если её нет» — это второе соединение, к служебной базе кластера (`postgres`),
# и там `CREATE DATABASE`. Право на это есть не у каждой роли, поэтому создание
# включает админ (галка в форме), а отказ объясняет, чего не хватило.
#
# Только по явной команде «Применить»: при старте сервиса (`bootstrap`) базу мы не
# заводим — иначе опечатка в имени тихо превращалась бы в пустой кабинет вместо
# громкой ошибки, а пустой кабинет в отчёте выглядит как «всё хорошо».
MAINTENANCE_DATABASE = "postgres"
SQLSTATE_NO_DATABASE = "3D000"        # invalid_catalog_name — «базы нет на сервере»
SQLSTATE_ALREADY_THERE = "42P04"      # duplicate_database — успел сосед
SQLSTATE_NO_RIGHTS = "42501"          # insufficient_privilege — роли не хватает CREATEDB


def is_postgres(candidate: dict[str, Any]) -> bool:
    return str(candidate.get("dialect") or "postgresql").strip().lower() != "sqlite"


def quote_ident(name: str) -> str:
    """Идентификатор для DDL: `CREATE DATABASE` не принимает связываемых параметров.

    Экранирование — двойная кавычка внутри: `we"ird` становится `"we""ird"`, и
    попытка протащить `"; DROP DATABASE …` остаётся именем базы, а не кодом.
    """
    if not name:
        raise ValueError("Имя базы не указано")
    if len(name) > 63:
        raise ValueError("Имя базы длиннее 63 символов — PostgreSQL не примет")
    if "\x00" in name:
        raise ValueError("Имя базы не может содержать нулевой символ")
    return '"' + name.replace('"', '""') + '"'


def maintenance_url(candidate: dict[str, Any]) -> str:
    """URL служебной базы того же кластера: те же хост, порт, роль и пароль."""
    if not is_postgres(candidate):
        raise ValueError("Служебная база есть только у PostgreSQL")
    return build_url({**candidate, "database": MAINTENANCE_DATABASE})


def _sqlstate(exc: BaseException) -> str:
    """SQLSTATE из драйвера: по нему «базы нет» отличается от «сервера нет»."""
    return str(getattr(getattr(exc, "orig", None), "pgcode", "") or "")


def _connect(url: str):
    """Одноразовое соединение: без пула и с коротким таймаутом (пул проверке не нужен)."""
    kwargs: dict[str, Any] = {}
    if not url.startswith("sqlite"):
        kwargs["connect_args"] = {"connect_timeout": TEST_TIMEOUT_SEC}
    return create_engine(url, poolclass=NullPool, pool_pre_ping=True, future=True, **kwargs)


def _lookup(candidate: dict[str, Any], sql: str):
    """Один вопрос к служебной базе кластера: (значение | None, ошибка | None)."""
    secret = str(candidate.get("password") or "")
    try:
        target = maintenance_url(candidate)
    except ValueError as exc:
        return None, str(exc)
    eng = None
    try:
        eng = _connect(target)
        with eng.connect() as conn:
            return conn.execute(text(sql), {"name": str(candidate.get("database") or "")}).first(), None
    except Exception as exc:  # noqa: BLE001 — наружу человеческий текст без пароля
        return None, _reason(exc, secret)
    finally:
        if eng is not None:
            eng.dispose()


def database_exists(candidate: dict[str, Any]) -> tuple[bool | None, str | None]:
    """Есть ли база. `None` — проверить не удалось: «не знаем» — это не «нет, создадим».

    Спрашиваем не саму базу (к неё как раз не подключиться), а `pg_database`
    служебной базы того же кластера. Ошибку отдаём текстом: админ должен видеть,
    что именно не ответило — роль, пароль, хост или файрвол.
    """
    if not is_postgres(candidate):
        path = str(candidate.get("database") or "")
        return (bool(path) and Path(path).exists()), None
    row, error = _lookup(candidate, "SELECT 1 FROM pg_database WHERE datname = :name")
    return (None if error else bool(row)), error


def create_database(candidate: dict[str, Any]) -> dict[str, Any]:
    """Создать базу, если её нет. Кроме `CREATE DATABASE`, не делает ничего.

    Владелец — роль из формы: база заводится её же соединением, поэтому `OWNER`
    не нужен и прав на членство в роли тоже. Кодировку смотрим сразу: кластер в
    `SQL_ASCII` честно примет данные, но кириллица в нём встанет криво, и знать
    об этом надо до переезда, а не из отчёта за месяц.
    """
    if not is_postgres(candidate):
        return {"created": False, "error": None,
                "note": "SQLite: файл создаст драйвер, отдельная база не нужна"}

    name = str(candidate.get("database") or "").strip()
    secret = str(candidate.get("password") or "")
    try:
        ident = quote_ident(name)
        target = maintenance_url(candidate)
    except ValueError as exc:
        return {"created": False, "error": str(exc), "note": None}

    eng = None
    encoding = None
    try:
        # `AUTOCOMMIT` обязателен: PostgreSQL отказывается выполнять
        # CREATE DATABASE внутри транзакции, а SQLAlchemy её открывает.
        eng = _connect(target)
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text(f"CREATE DATABASE {ident}"))
            encoding = conn.execute(
                text("SELECT pg_encoding_to_char(encoding) FROM pg_database WHERE datname = :name"),
                {"name": name},
            ).scalar()
    except Exception as exc:  # noqa: BLE001 — драйверы говорят по-разному
        if _sqlstate(exc) == SQLSTATE_ALREADY_THERE or "already exists" in str(exc).lower():
            return {"created": False, "error": None, "note": "база уже существовала"}
        message = _reason(exc, secret)
        user = str(candidate.get("user") or "")
        if _sqlstate(exc) == SQLSTATE_NO_RIGHTS or "permission denied" in message.lower():
            message += (f" — роль «{user}» не умеет создавать базы; дайте "
                        f"GRANT CREATEDB TO {quote_ident(user)}, либо заведите базу вручную: "
                        f"createdb -h {candidate.get('host')} -U {user} {name}")
        return {"created": False, "error": message, "note": None}
    finally:
        if eng is not None:
            eng.dispose()

    log.info("Создана база «%s» на %s:%s", name, candidate.get("host"), candidate.get("port"))
    note = None
    if encoding and str(encoding).upper() not in ("UTF8", "UNICODE"):
        note = f"база создана в кодировке {encoding}: кириллица может встать криво"
    return {"created": True, "error": None, "note": note}


# ------------------------------------------------------------------- проверка ----

def test(candidate: dict[str, Any]) -> dict[str, Any]:
    """Подключиться к кандидату и ничего не менять в кабинете.

    Движок — отдельный, без пула (`NullPool`) и с коротким таймаутом соединения:
    проверке пул не нужен, а висящий порт не должен вешать поток запроса.
    Для sqlite проверка создаёт файл, если его нет: это и есть честный ответ
    «подключиться можно».

    `exists` отвечает на вопрос «базы нет или сервер не отвечает»: `False` —
    именно нет (SQLSTATE `3D000`), `None` — проверить не удалось. Разница важна
    форме: только на `False` честен совет «создать базу, если её нет».
    """
    def answer(**fields: Any) -> dict[str, Any]:
        return {"version": None, "error": None, "exists": None, "note": None,
                "url": masked(candidate), **fields}

    try:
        url = build_url(candidate)
    except ValueError as exc:
        return answer(ok=False, error=str(exc))

    secret = str(candidate.get("password") or "")
    eng = None
    try:
        eng = _connect(url)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
            info = eng.dialect.server_version_info
        version = (".".join(str(part) for part in info) if isinstance(info, tuple)
                   else str(info or "—"))
        return answer(ok=True, version=version, exists=True)
    except Exception as exc:  # noqa: BLE001 — драйверы говорят по-разному, наружу — текст
        reason = _reason(exc, secret)
        if _sqlstate(exc) == SQLSTATE_NO_DATABASE:
            return answer(ok=False, exists=False, error=reason,
                          note="базы нет на сервере — включите «создать базу, если её нет» и примените")
        return answer(ok=False, error=reason)
    finally:
        if eng is not None:
            eng.dispose()


def _reason(exc: BaseException, secret: str = "") -> str:
    """Текст ошибки без пароля: SQLAlchemy печатает URL соединения в сообщении."""
    message = f"{type(exc).__name__}: {exc}".replace("\n", " ")
    for variant in {secret, quote(secret, safe="")} - {""}:
        message = message.replace(variant, "***")
    return message[:300]


# -------------------------------------------------------------------- переезд ----

def _store(db: Session, candidate: dict[str, Any], enabled: bool, actor: User | None) -> None:
    """Сохранить части в стор настроек (валидация и аудит — за `app_settings`)."""
    patch: dict[str, Any] = {
        KEY_DIALECT: str(candidate.get("dialect") or "postgresql").strip().lower(),
        KEY_HOST: str(candidate.get("host") or "").strip(),
        KEY_PORT: int(candidate.get("port") or 5432),
        KEY_USER: str(candidate.get("user") or "").strip(),
        KEY_DATABASE: str(candidate.get("database") or "").strip(),
        KEY_ENABLED: enabled,
    }
    password = candidate.get("password")
    if password is not None and str(password).strip() != MASK:
        patch[KEY_PASSWORD] = str(password)
    app_settings.apply(db, patch, actor)


def apply(db: Session, candidate: dict[str, Any], actor: User | None = None) -> dict[str, Any]:
    """Сохранить части и переехать на новую базу прямо сейчас.

    Сначала переезд, потом сохранение: не удалось — кабинет остаётся на прежней
    базе, и `source` честно отвечает `env`. Указатель храним в ТОЙ базе, с которой
    админ пришёл (она же bootstrap): именно её кабинет читает первой после
    старта, поэтому решение «жить на другой базе» обязано лежать там.

    `create_missing` — разовая просьба «создать базу, если её нет» (галка в
    форме). Она не сохраняется в стор настроек: завтра админ поправит хост, и
    молча созданная пустая база на новом сервере была бы хуже честного отказа.
    Не удалось создать — отказ до первого изменения кабинета, в `ValueError`.
    """
    full = with_password(db, candidate)
    url = build_url(full)                       # падает до первого изменения в БД
    created = False
    if candidate.get("create_missing"):
        outcome = create_database(full)
        if outcome["error"]:
            # причину возвращаем голую — к ней добавляем, ЧЕГО именно не вышло
            raise ValueError(f"создать базу «{full.get('database')}» не удалось: {outcome['error']}")
        created = bool(outcome["created"])
    rebuild_engine(url, secret=str(full.get("password") or ""))
    admin_created = _ensure_admin_on_new_base()
    _store(db, full, enabled=True, actor=actor)
    log.info("База кабинета переключена на %s", masked_url())
    return {"url": display_url(), "version": server_version(), "source": SOURCE_SETTINGS,
            "created": created, "admin_created": admin_created}


def _ensure_admin_on_new_base() -> bool:
    """На пустой базе некого пускать.

    После переезда текущий вход умирает (пользователей знает прежняя база), и если
    на новой базе нет ни одного пользователя, «перезайдите заново» превращается в
    «в кабинет не попасть». `ensure_admin` создаёт администратора только при пустой
    таблице пользователей — базу с живыми людьми он не трогает.
    """
    from ..seed import ensure_admin                    # cyclic-safe: seed читает .db

    with SessionLocal() as fresh:
        temp = ensure_admin(fresh)
    if temp:
        log.warning("На новой базе создан администратор «%s» с паролем «%s». "
                    "Смените его сразу после входа.",
                    get_settings().admin_login, temp)
        return True
    return False


def revert(db: Session, actor: User | None = None) -> dict[str, Any]:
    """Вернуться к `DATABASE_URL` из окружения (кнопка «вернуть ENV»).

    Флаг источника снимаем НОВОЙ сессией: сессия запроса привязана к той базе,
    с которой админ пришёл, а после переезда она уже не текущая.
    """
    rebuild_engine(env_url())
    with SessionLocal() as fresh:
        app_settings.apply(fresh, {KEY_ENABLED: False}, actor)
    log.info("Кабинет вернулся к базе из окружения: %s", masked_url())
    return {"url": display_url(), "version": server_version(), "source": SOURCE_ENV}


def bootstrap(db: Session) -> dict[str, Any] | None:
    """Продолжить старт на базе из настроек, если админ когда-то её указал.

    Порядок старта замкнутый: подключиться некуда, кроме как к `DATABASE_URL`, а
    «мы уже не там» знает только первая база. Поэтому кабинет поднимается на
    bootstrap-URL, читает её настройки и переезжает до планировщика, админов и
    витрины (п. 27 TASKS2.md).

    Ошибка переезда — не причина не запускаться: кабинет остаётся на
    bootstrap-базе и громко пишет об этом в лог.
    """
    if not pointer(db):
        return None
    candidate = parts(db)
    candidate["password"] = app_settings.get(db, KEY_PASSWORD) or ""
    try:
        url = build_url(candidate)
    except ValueError as exc:
        return {"error": str(exc)}
    if current_engine_url() == make_url(url):
        return None                                  # и так уже там
    try:
        rebuild_engine(url, secret=str(candidate["password"]))
    except EngineSwapError as exc:
        return {"error": str(exc)}
    return {"url": display_url(), "version": server_version(), "source": SOURCE_SETTINGS}


def public(db: Session) -> dict[str, Any]:
    """Сводка для вкладки «База данных»: части, источник, версия сервера.

    Части описывают ту базу, на которой кабинет стоит СЕЙЧАС: на env-базе это
    `DATABASE_URL`, а не пустые части из стора. Иначе форма стартовала бы с
    «postgresql, всё пусто», а первое «Применить» молча сменило бы диалект.
    """
    src = source(db)
    return {
        "source": src,
        "url": display_url(),
        "server_version": server_version(),
        "parts": parts(db) if src == SOURCE_SETTINGS else env_parts(),
        "password_set": password_set(db),
        "env_url": display_env_url(),
        "env_parts": env_parts(),
    }


__all__ = ["SOURCE_ENV", "SOURCE_SETTINGS", "apply", "bootstrap", "build_url", "create_database",
           "database_exists", "display_env_url", "display_url", "env_parts", "env_url", "masked",
           "maintenance_url", "parts", "password_set", "pointer", "public", "quote_ident",
           "revert", "source", "test", "with_password"]
