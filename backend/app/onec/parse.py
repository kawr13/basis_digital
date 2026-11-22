"""Разбор «плоских» строк 1С в записи контракта.

Один разбор на два сценария:
  * кабинет сам сходил в HTTP-сервис 1С (http_source.py, ключ `items`);
  * 1С сама прислала срез на POST /api/onec/push (ключи `warehouses`/`balance`/...).

Поля здесь, а не в Pydantic-схемах, потому что контракт намеренно «плоский» и
терпимый к отсутствующим полям: 1С отдаёт готовые строки, а кабинет приводит
типы и не падает от пустых значений (см. docs/03_контракт_1с.md).
Одна битая строка не роняет срез: её пропускаем и кладём в warnings; форма
ответа или больше половины битых строк раздела — уже сломанный контракт, ошибка.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from .base import CapacityRec, CargoRec, DynamicsRec, Snapshot, WarehouseRec


def parse_dt(value: Any) -> datetime:
    """ISO8601 или «12.10.2026 18:00»-подобная строка → наивный UTC."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if not value:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    text = str(value).strip().replace("T", " ").replace("Z", "")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.fromisoformat(text.split(" ")[0])
        except ValueError as exc:
            raise ValueError(f"не разобрали дату «{value}»") from exc


def _num(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(str(value).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return default


def _text(value: Any, default: str = "") -> str:
    return str(value).strip() if value not in (None, "") else default


# Индивидуальных предупреждений на раздел: один мусорный ответ 1С не должен
# раздувать журнал синхронизации, сверх лимита — одна свёрнутая строка.
_MAX_SECTION_WARNINGS = 50
_MAX_WARNING_LEN = 160


class _SectionWarn:
    """Сборщик предупреждений раздела: префикс, номер строки, лимит, свёртка."""

    def __init__(self, label: str, sink: list[str] | None) -> None:
        self.label = label
        # Список не передан — собираем в локальный и выбрасываем: вызывающий код
        # вне parse.py не обязан знать про warnings.
        self._sink: list[str] = [] if sink is None else sink
        self._emitted = 0
        self._overflow = 0

    def add(self, row_no: int, reason: str) -> None:
        msg = f"{self.label}, строка {row_no}: {reason}"
        if len(msg) > _MAX_WARNING_LEN:
            msg = msg[:_MAX_WARNING_LEN - 1] + "…"
        if self._emitted < _MAX_SECTION_WARNINGS:
            self._sink.append(msg)
            self._emitted += 1
        else:
            self._overflow += 1

    def finish(self, skipped: int, total: int) -> None:
        # Больше половины битых — это не «потерялись пара строк», а «1С отдала
        # мусор»: лучше сорвать синхронизацию, чем молча положить половину витрины.
        if skipped * 2 > total:
            raise ValueError(
                f"{self.label}: пропущено {skipped} из {total} строк — "
                "битых больше половины, раздел отвергнут"
            )
        if self._overflow:
            self._sink.append(f"{self.label}: …и ещё {self._overflow} таких же")


def _rows(items: Any) -> list[dict]:
    """Разрешаем и `items`, и именованный массив, и просто список строк.
    Форма ответа строгая: не список или строка-не-объект означают, что 1С
    нарушила контракт (это ошибка синхронизации), в отличие от отдельных битых строк.
    """
    if items is None:
        return []
    if isinstance(items, dict):                      # {"items": [...]}
        items = items.get("items", [])
    if not isinstance(items, list):
        raise ValueError("ожидался список строк")
    rows = []
    for row in items:
        if not isinstance(row, dict):
            raise ValueError("строка среза должна быть объектом")
        rows.append(row)
    return rows


def parse_warehouses(items: Any, warnings: list[str] | None = None) -> list[WarehouseRec]:
    sec = _SectionWarn("склады (warehouses)", warnings)
    rows = _rows(items)
    out: list[WarehouseRec] = []
    for row_no, row in enumerate(rows, start=1):
        code = _text(row.get("code") or row.get("warehouse_code"))
        if not code:
            sec.add(row_no, "нет code")
            continue
        out.append(WarehouseRec(
            code=code,
            name=_text(row.get("name"), code),
            kind=_text(row.get("kind"), "warehouse"),
            capacity=_num(row.get("capacity")),
            unit=_text(row.get("unit"), "т"),
        ))
    sec.finish(len(rows) - len(out), len(rows))
    return out


def parse_cargo(items: Any, warnings: list[str] | None = None) -> list[CargoRec]:
    sec = _SectionWarn("остатки (balance)", warnings)
    rows = _rows(items)
    out: list[CargoRec] = []
    for row_no, row in enumerate(rows, start=1):
        code = _text(row.get("warehouse_code") or row.get("code"))
        if not code:
            sec.add(row_no, "нет warehouse_code")
            continue
        out.append(CargoRec(
            warehouse_code=code,
            cargo_group=_text(row.get("cargo_group"), "Без группы"),
            qty=_num(row.get("qty")),
            weight_t=_num(row.get("weight_t")),
            overdue_weight_t=_num(row.get("overdue_weight_t")),
            avg_age_days=_num(row.get("avg_age_days")),
        ))
    sec.finish(len(rows) - len(out), len(rows))
    return out


def parse_capacity(items: Any, warnings: list[str] | None = None) -> list[CapacityRec]:
    sec = _SectionWarn("ёмкость (capacity)", warnings)
    rows = _rows(items)
    out: list[CapacityRec] = []
    for row_no, row in enumerate(rows, start=1):
        code = _text(row.get("warehouse_code") or row.get("code"))
        if not code:
            sec.add(row_no, "нет warehouse_code")
            continue
        out.append(CapacityRec(
            warehouse_code=code,
            used=_num(row.get("used")),
            capacity=_num(row.get("capacity")),
            unit=_text(row.get("unit"), "т"),
        ))
    sec.finish(len(rows) - len(out), len(rows))
    return out


def parse_dynamics(items: Any, warnings: list[str] | None = None) -> list[DynamicsRec]:
    sec = _SectionWarn("динамика (dynamics)", warnings)
    rows = _rows(items)
    out: list[DynamicsRec] = []
    for row_no, row in enumerate(rows, start=1):
        code = _text(row.get("warehouse_code") or row.get("code"))
        if not code:
            sec.add(row_no, "нет warehouse_code")
            continue
        raw_date = row.get("date") or row.get("bucket_date")
        if not raw_date:
            sec.add(row_no, "нет date")
            continue
        try:
            bucket = date.fromisoformat(str(raw_date).strip()[:10])
        except ValueError:
            sec.add(row_no, f"дата не YYYY-MM-DD: {raw_date}")
            continue
        out.append(DynamicsRec(
            warehouse_code=code,
            bucket_date=bucket,
            inbound_t=_num(row.get("inbound_t")),
            outbound_t=_num(row.get("outbound_t")),
            processed_t=_num(row.get("processed_t")),
            docs_count=int(_num(row.get("docs_count"))),
            avg_dwell_hours=_num(row.get("avg_dwell_hours")),
        ))
    sec.finish(len(rows) - len(out), len(rows))
    return out


def snapshot_from_payload(data: Any) -> Snapshot:
    """Срез из тела push-запроса (тот же контракт, что отдаёт HTTP-сервис).

    Битые строки не роняют срез: они пропускаются, а причины собираются в
    Snapshot.warnings.
    """
    if not isinstance(data, dict):
        raise ValueError("ожидался JSON-объект со срезом")

    warnings: list[str] = []
    warehouses = parse_warehouses(data.get("warehouses", data.get("items")), warnings)
    cargo = parse_cargo(data.get("balance", data.get("cargo")), warnings)
    capacity = parse_capacity(data.get("capacity"), warnings)
    dynamics = parse_dynamics(data.get("dynamics"), warnings)

    if not (warehouses or cargo or capacity or dynamics):
        raise ValueError("пустой срез: ни одной строки в warehouses/balance/capacity/dynamics")

    return Snapshot(
        snapshot_at=parse_dt(data.get("snapshot_at")),
        warehouses=warehouses,
        cargo=cargo,
        capacity=capacity,
        dynamics=dynamics,
        warnings=warnings,
    )
