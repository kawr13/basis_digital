<#
  pack_release.ps1 — собрать чистый архив проекта для отправки на сервер.

  Что делает: копирует дерево statcab/ в staging, вырезая мусор (node_modules,
  dist, .git, локальные БД, логи, секреты .env), и пакует в zip. В корень
  архива кладёт RELEASE-INFO.txt (версия, дата, ветка/коммит, кто собрал),
  рядом с архивом — его копию и <архив>.sha256 в формате sha256sum, чтобы на
  сервере проверить целостность пересылки (TASKS2.md, п. 33).

  Чистота при сбое: staging и недопечатанный zip удаляются даже при падении
  (finally + catch), любой сбой даёт ненулевой код возврата — в release\ не
  остаётся ни мусора, ни «полураспакованного» релиза.

  Запуск:  powershell -ExecutionPolicy Bypass -File .\pack_release.ps1
  Результат: release\statcab-<дата-времени>.zip
             release\statcab-<дата-времени>.zip.sha256
             release\RELEASE-INFO.txt (та же копия, что внутри архива)

  ВАЖНО: .env в архив НЕ попадает — на сервере он создаётся из .env.example.
#>
$ErrorActionPreference = "Stop"

$root       = $PSScriptRoot
$stamp      = Get-Date -Format "yyyyMMdd-HHmm"
$stage      = Join-Path $root "release\statcab"
$zip        = Join-Path $root "release\statcab-$stamp.zip"
$zipName    = Split-Path $zip -Leaf
$releaseDir = Split-Path $zip -Parent

# Каталоги, вырезаемые на любой глубине.
$excludeDirs = @(
    '.git', 'node_modules', 'dist', '__pycache__', '.pytest_cache',
    '.venv', 'venv', 'data', 'release', '.idea', '.vscode'
)
# Файлы-маски, вырезаемые на любой глубине.
$excludeFiles = @('.env', '*.db', '*.sqlite', '*.log', 'shot_*.png',
                  '*.tsbuildinfo', '*.pyc', 'uvicorn-dev.log')

# ---------- метаданные сборки ----------
# RELEASE-INFO.txt кладётся ВНУТРЬ архива, поэтому собираем его до упаковки.
# git недоступен или это не репозиторий — пишем unknown и живём дальше:
# метаданные полезны, но рвать из-за них сборку релиза не за чем.
$branch = 'unknown'
$commit = 'unknown'
if (Get-Command git -ErrorAction SilentlyContinue) {
    # git печатает штатные сообщения в stderr, а PowerShell с
    # $ErrorActionPreference=Stop превращает их в terminating error —
    # на время вызова снижаем строгость.
    $eap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $commit = (& git -C $root rev-parse --short HEAD 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $commit) { $commit = 'unknown' }
        $branch = (& git -C $root rev-parse --abbrev-ref HEAD 2>$null | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $branch) { $branch = 'unknown' }
    } catch {
        $branch = 'unknown'; $commit = 'unknown'
    } finally {
        $ErrorActionPreference = $eap
    }
}

# Версия — из frontend/package.json (та же, что отдаёт FastAPI в /openapi.json).
$version = 'unknown'
try {
    $pkg = Get-Content (Join-Path $root 'frontend\package.json') -Raw | ConvertFrom-Json
    if ($pkg.version) { $version = $pkg.version }
} catch { }

$who = if ($env:USERNAME) { $env:USERNAME } else { 'unknown' }
$utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss") + ' UTC'

# UTF-8 без BOM и переводы строк LF: файлы читают утилиты на Linux-сервере;
# BOM ломает sha256sum -c по первой строке, а CRLF добавляет \r к имени файла.
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

# Счётчик файлов виден и внутри try, и в финальных сообщениях после него.
$copied = 0

try {
    if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
    New-Item -ItemType Directory -Path $stage | Out-Null

    $items = Get-ChildItem -Path $root -Recurse -Force | Where-Object {
        $rel   = $_.FullName.Substring($root.Length).TrimStart('\','/')
        $parts = $rel -split '[\\/]'
        $skip  = $false
        foreach ($d in $excludeDirs) { if ($parts -contains $d) { $skip = $true; break } }
        if (-not $skip -and -not $_.PSIsContainer) {
            foreach ($f in $excludeFiles) { if ($_.Name -like $f) { $skip = $true; break } }
        }
        -not $skip
    }

    foreach ($it in $items) {
        $rel = $it.FullName.Substring($root.Length).TrimStart('\','/')
        $dst = Join-Path $stage $rel
        if ($it.PSIsContainer) {
            New-Item -ItemType Directory -Path $dst -Force | Out-Null
        } else {
            New-Item -ItemType Directory -Path (Split-Path $dst) -Force | Out-Null
            Copy-Item $it.FullName $dst
            $copied++
        }
    }

    # RELEASE-INFO.txt — в корень staging, т.е. внутри архива как
    # statcab/RELEASE-INFO.txt. Хеш архива сюда не вписываем: архива ещё нет
    # (он — в <архив>.sha256 рядом).
    $infoLines = @(
        'StatCab — информация о сборке релизного архива',
        "версия:         $version",
        "архив:          $zipName",
        "сборка:         $utc",
        "ветка/коммит:   $branch @ $commit",
        "собрал:         $who",
        "файлов проекта: $copied"
    )
    $infoText = ($infoLines -join "`n") + "`n"
    [System.IO.File]::WriteAllText((Join-Path $stage 'RELEASE-INFO.txt'), $infoText, $utf8NoBom)

    # Compress-Archive и ZipFile::CreateFromDirectory в Windows PowerShell пишут
    # в zip backslash-пути — linux- unzip распакует их файлами-уродами.
    # Пишем записи вручную через CreateEntry с прямыми слешами.
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    if (Test-Path $zip) { Remove-Item $zip -Force }
    try {
        $fs  = [System.IO.File]::Open($zip, [System.IO.FileMode]::Create)
        $arc = New-Object System.IO.Compression.ZipArchive($fs, [System.IO.Compression.ZipArchiveMode]::Create)
        try {
            Get-ChildItem $stage -Recurse -File | ForEach-Object {
                $rel = ('statcab/' + $_.FullName.Substring($stage.Length).TrimStart('\','/')) -replace '\\', '/'
                [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($arc, $_.FullName, $rel) | Out-Null
            }
        } finally {
            # Dispose по частям: если умрёт $arc.Dispose(), поток $fs всё равно
            # закроется и файл не останется залоченным.
            try { $arc.Dispose() } finally { $fs.Dispose() }
        }
    } catch {
        # Недопечатанный zip — мусор: на сервере его никто не проверит,
        # а по размеру от настоящего не отличить. Осиротевший .sha256
        # прошлого запуска (тот же штамп минуты) — тоже.
        if (Test-Path $zip)          { Remove-Item $zip -Force }
        if (Test-Path "${zip}.sha256") { Remove-Item "${zip}.sha256" -Force }
        throw
    }

    # ---------- SHA256 (TASKS2.md, п. 33) ----------
    $hashHex = (Get-FileHash -Algorithm SHA256 -Path $zip).Hash.ToLowerInvariant()
    # Формат совместим с `sha256sum -c`: hash + два пробела + имя файла.
    [System.IO.File]::WriteAllText("${zip}.sha256", "$hashHex  $zipName`n", $utf8NoBom)
    # Копия RELEASE-INFO рядом с архивом — смотреть, не распаковывая его.
    [System.IO.File]::WriteAllText((Join-Path $releaseDir 'RELEASE-INFO.txt'), $infoText, $utf8NoBom)

    $size = [math]::Round((Get-Item $zip).Length / 1MB, 2)
    Write-Host ""
    Write-Host "Готово: $zip ($copied файлов, $size МБ)" -ForegroundColor Green
    Write-Host "sha256: $hashHex" -ForegroundColor Green
    Write-Host "Рядом: ${zipName}.sha256 (проверка: sha256sum -c ${zipName}.sha256) и RELEASE-INFO.txt" -ForegroundColor Green
    Write-Host ""
    Write-Host "Чек-лист перед отправкой:" -ForegroundColor Yellow
    Write-Host "  1. .env в архиве нет — на сервере: cp .env.example .env и заполнить."
    Write-Host "  2. SECRET_KEY и POSTGRES_PASSWORD сгенерировать: openssl rand -hex 32"
    Write-Host "  3. Сервер без HTTPS -> в .env поставить COOKIE_SECURE=false."
    Write-Host "  4. Ручной старт на сервере: docker compose up -d --build"
    Write-Host "     Подробности: docs/06_деплой_на_сервер.md"

    exit 0
} catch {
    # Скрипт дёргают из цепочек (bat, CI): «упало, но вернуло 0» — худший
    # исход, на сервер уедет пустота или прошлый архив.
    Write-Host "СБОЙ: сборка релиза не завершена: $_" -ForegroundColor Red
    exit 1
} finally {
    # Провал на любом шаге — staging не должен остаться в release\:
    # полукопия дерева неотличима от настоящего содержимого архива.
    if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
}
