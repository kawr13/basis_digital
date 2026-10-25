"""Единый формат CSV-выгрузок кабинета.

Писатель CSV исторически жил в `api/reports.py`. Как только появилась вторая
выгрузка — строки дриллдауна «Статистики» (`/api/dispatcher/rows.csv`), — формат
уехал в общий модуль: русская Excel ждёт разделитель `;` и запятую в дробях, а
без BOM кириллица открывается «кракозябрами». Держим одну реализацию: две копии
разъезжаются при первой же правке, и пользователь начинает чинить файл после
каждой второй кнопки «CSV».
"""
from __future__ import annotations

import csv
import io
import re
import urllib.parse
from typing import Iterable, Sequence

from fastapi import Response

# Символы, которые нельзя ни в имени файла Windows, ни в заголовке HTTP.
_UNSAFE = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def to_csv_response(headers: Sequence[str], rows: Iterable[Sequence[object]],
                    filename: str) -> Response:
    """CSV для русскоязычного Excel: разделитель «;», десятичный «,», BOM в начале."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(list(headers))
    for row in rows:
        writer.writerow([
            f"{v:.1f}".replace(".", ",") if isinstance(v, float) else v for v in row
        ])
    return Response(
        content="\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": content_disposition(filename)},
    )


def content_disposition(filename: str) -> str:
    """Заголовок отдачи файла: ASCII-дубль + UTF-8 по RFC 5987.

    Имена выгрузок строятся из разреза («РП - 1/2», «Груз · уголь»), поэтому в
    них живут и кириллица, и символы, запрещённые в имени файла. Экран имя себе
    придумывает сам (`a.download`), а вот curl и «Сохранить ссылку как» читают
    именно этот заголовок: он кодируется latin-1, поэтому в `filename=` едет
    ASCII-дубль (разборки судов по-русски в нём теряются), а оригинал — в
    `filename*`, который понимают и Excel, и современные браузеры.
    """
    folded = _UNSAFE.sub("_", filename).encode("ascii", "ignore").decode()
    safe = re.sub(r"\s+", "_", folded).strip("._") or "export.csv"
    return (f'attachment; filename="{safe}"; '
            f"filename*=UTF-8''{urllib.parse.quote(filename)}")
