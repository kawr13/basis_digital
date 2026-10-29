# 07. Docker на рабочей машине (Windows)

Задача: поднять PostgreSQL 16 в Docker, чтобы прогнать бенчмарк P.12 из
`TASKS.md` на той же СУБД, что в проде.

Состояние машины на момент записи (29.10.2026):

| Компонент | Состояние |
|---|---|
| Docker Desktop | установлен (`C:\Program Files\Docker\Docker\Docker Desktop.exe`), клиент 29.x |
| Демон Docker | **не стартует** — WSL2 не поддерживается конфигурацией компьютера |
| Виртуализация в BIOS | **выключена** (`Win32_Processor.VMMonitorModeExtensions = False`) |
| Компонент «Платформа виртуальных машин» | не включён |

Вывод: единственное, что не сделать из-под пользователя, — **включить
виртуализацию в BIOS**. Всё остальное автоматизировано скриптом.

---

## 1. Включить виртуализацию в BIOS (руками, один раз)

1. Перезагрузить компьютер.
2. Зайти в BIOS (обычно `Del`/`F2` при старте).
3. Включить виртуализацию:
   * Intel — **Intel Virtualization Technology (VT-x)**, раздел CPU/Advanced;
   * AMD — **SVM Mode**, раздел CPU/Advanced.
4. Сохранить и перезагрузиться.

Проверка после включения (без админки):

```powershell
(Get-CimInstance Win32_Processor).VMMonitorModeExtensions
# должно быть True
```

## 2. Включить компоненты Windows (админская PowerShell)

```powershell
cd D:\basis\statcab
powershell -ExecutionPolicy Bypass -File .\tools\docker_setup.ps1
```

Скрипт: проверяет права, печатает состояние «до», включает
`VirtualMachinePlatform` и `HypervisorPlatform`, ставит WSL2 версией по
умолчанию, напоминает про перезагрузку. Если в BIOS уже включили, скрипт
можно прогнать повторно — он идемпотентен.

После скрипта — **перезагрузка** (если не перезагружались после BIOS).

## 3. Запустить Docker Desktop

1. Пиктограмма кита в трее → дождаться «Docker Desktop is running».
2. Проверить:

```powershell
docker run --rm hello-world
docker info | findstr /i "server version"
```

Если демон всё равно не стартует — `Docker Desktop → Troubleshoot → Restart`,
и журнал: `Docker Desktop → Troubleshoot → View logs`.

## 4. PostgreSQL для бенчмарка P.12

```powershell
cd D:\basis\statcab
powershell -ExecutionPolicy Bypass -File .\tools\pg_up.ps1
```

Скрипт поднимает отдельный контейнер `statcab-pg-bench`
(`postgres:16-alpine`, порт `5432`, том `statcab_pg_bench_data`) — **не**
сервис `db` из `docker-compose.yml`: compose-база живёт в своей сети и порт
на хост не пробрасывает, а бэкенд для бенчмарка должен доехать до базы с
хоста. Скрипт напечатает `DATABASE_URL` — вставить в `backend\.env`,
поставить драйвер:

```powershell
cd backend
.venv\Scripts\pip install psycopg2-binary
```

Остановка/сброс:

```powershell
docker stop statcab-pg-bench                                  # данные сохранятся
docker rm -f statcab-pg-bench; docker volume rm statcab_pg_bench_data   # полный сброс
```

## 5. Прогнать бенчмарк

Тот же сценарий, что на SQLite (P.10 из `TASKS.md`), но с `DATABASE_URL` на
`127.0.0.1:5432`. Снять `EXPLAIN` (и `EXPLAIN (ANALYZE, BUFFERS)`) для
медленных запросов — их список в `TASKS.md`, пункт 12.

---

## Если что-то пошло не так

| Симптом | Причина / что делать |
|---|---|
| `WSL2 is not supported` в логе Docker Desktop | виртуализация в BIOS не включилась — п. 1, проверить `VMMonitorModeExtensions` |
| `error during connect ... dockerDesktopLinuxEngine` | демон не запущен — п. 3 |
| `port is already allocated` в `pg_up.ps1` | 5432 занят (например, локальный Postgres) — остановить его или сменить порт в скрипте и в `DATABASE_URL` синхронно |
| Docker Desktop требует WSL-дистрибутив при старте | он установит сам (`docker-desktop`, `docker-desktop-data`); если нет — `wsl --install --no-distribution` из админской консоли |
