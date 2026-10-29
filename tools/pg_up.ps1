#Requires -Version 5.1
<#
.pg_up.ps1 — поднять PostgreSQL 16 в Docker под бенчмарк P.12 (TASKS.md).

Отдельный контейнер `statcab-pg-bench`, а НЕ сервис db из docker-compose.yml:
compose-база живёт в своей сети и не пробрасывает 5432 на хост, а для
бенчмарка бэкенд должен доехать до базы с хоста (127.0.0.1:5432).
Производственный compose скрипт не трогает.

Скрипт:
  * проверяет, что Docker-демон жив;
  * поднимает postgres:16-alpine с портом 5432 и томом statcab_pg_bench_data;
  * ждёт, пока pg_isready ответит;
  * печатает DATABASE_URL, который вставить в backend\.env.

Запуск:
    cd D:\basis\statcab
    powershell -ExecutionPolicy Bypass -File .\tools\pg_up.ps1
Остановка (данные в томе сохранятся):
    docker stop statcab-pg-bench
Полный сброс (данные тоже):
    docker rm -f statcab-pg-bench && docker volume rm statcab_pg_bench_data
#>
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$NAME = 'statcab-pg-bench'
$VOL = 'statcab_pg_bench_data'

function Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

# ── 0. Docker жив? ─────────────────────────────────────────────────────────
Step "Проверка Docker-демона"
$ver = & docker version --format '{{.Server.Version}}' 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker-демон не отвечает: $ver" -ForegroundColor Red
    Write-Host "Запустите Docker Desktop и дождитесь «Docker Desktop is running»." -ForegroundColor Red
    Write-Host "Если демон не стартует — tools\docker_setup.ps1 и docs/07." -ForegroundColor Red
    exit 1
}
Write-Host ("  Docker Engine " + $ver)

# ── 1. Пароль: переиспользуем, если контейнер уже поднимался ───────────────
$pass = $env:STATCAB_PG_PASS
if (-not $pass) {
    $pass = -join ((48..57, 97..122) | Get-Random -Count 20 | ForEach-Object { [char]$_ })
}

# ── 2. Поднять контейнер (идемпотентно) ────────────────────────────────────
Step "Контейнер $NAME (postgres:16-alpine, порт 5432)"
$exists = (& docker ps -a --format '{{.Names}}' | Select-String -SimpleMatch $NAME) -ne $null
if ($exists) {
    Write-Host "  Контейнер уже есть — запускаю"
    & docker start $NAME | Out-Null
} else {
    Write-Host "  Тяну образ и создаю контейнер (первый раз — может занять минуты)"
    & docker run -d --name $NAME `
        -e POSTGRES_DB=statcab -e POSTGRES_USER=statcab -e POSTGRES_PASSWORD=$pass `
        -p 5432:5432 -v "${VOL}:/var/lib/postgresql/data" `
        --restart unless-stopped postgres:16-alpine
    if ($LASTEXITCODE -ne 0) { Write-Host "docker run не прошёл" -ForegroundColor Red; exit 1 }
}

# ── 3. Ждём, пока база ответит (до 90 с) ───────────────────────────────────
Step "Ожидаем, пока pg_isready ответит"
$ok = $false
for ($i = 0; $i -lt 18; $i++) {
    Start-Sleep -Seconds 5
    $r = & docker exec $NAME pg_isready -U statcab 2>&1
    if ($r -match 'accepting connections') { $ok = $true; break }
    Write-Host ("  " + (($r | Select-Object -First 1)) + " ... ещё " + (18 - $i) * 5 + " с")
}
if (-not $ok) {
    Write-Host "База не ответила за 90 с: docker logs $NAME" -ForegroundColor Red
    exit 1
}
Write-Host "  accepting connections"

# ── 4. DATABASE_URL ────────────────────────────────────────────────────────
Step "DATABASE_URL для backend\.env"
Write-Host @"
  # добавить/заменить в backend\.env (путь 127.0.0.1:5432):
  DATABASE_URL=postgresql+psycopg2://statcab:$pass@127.0.0.1:5432/statcab

  Если драйвера PostgreSQL в .venv ещё нет:
    cd backend
    .venv\Scripts\pip install psycopg2-binary

  Проверка из psql:
    docker exec -it $NAME psql -U statcab -c 'select version();'
"@
