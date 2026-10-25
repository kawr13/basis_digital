<#
  pack_release.ps1 — собрать чистый архив проекта для отправки на сервер.

  Что делает: копирует дерево statcab/ в staging, вырезая мусор (node_modules,
  dist, .git, локальные БД, логи, секреты .env), и пакует в zip.

  Запуск:  powershell -ExecutionPolicy Bypass -File .\pack_release.ps1
  Результат: release\statcab-<дата-времени>.zip

  ВАЖНО: .env в архив НЕ попадает — на сервере он создаётся из .env.example.
#>
$ErrorActionPreference = "Stop"

$root  = $PSScriptRoot
$stamp = Get-Date -Format "yyyyMMdd-HHmm"
$stage = Join-Path $root "release\statcab"
$zip   = Join-Path $root "release\statcab-$stamp.zip"

# Каталоги, вырезаемые на любой глубине.
$excludeDirs = @(
    '.git', 'node_modules', 'dist', '__pycache__', '.pytest_cache',
    '.venv', 'venv', 'data', 'release', '.idea', '.vscode'
)
# Файлы-маски, вырезаемые на любой глубине.
$excludeFiles = @('.env', '*.db', '*.sqlite', '*.log', 'shot_*.png',
                  '*.tsbuildinfo', '*.pyc', 'uvicorn-dev.log')

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

$copied = 0
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

# Compress-Archive и ZipFile::CreateFromDirectory в Windows PowerShell пишут
# в zip backslash-пути — linux- unzip распакует их файлами-уродами.
# Пишем записи вручную через CreateEntry с прямыми слешами.
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
if (Test-Path $zip) { Remove-Item $zip -Force }
$fs  = [System.IO.File]::Open($zip, [System.IO.FileMode]::Create)
$arc = New-Object System.IO.Compression.ZipArchive($fs, [System.IO.Compression.ZipArchiveMode]::Create)
Get-ChildItem $stage -Recurse -File | ForEach-Object {
    $rel = ('statcab/' + $_.FullName.Substring($stage.Length).TrimStart('\','/')) -replace '\\', '/'
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($arc, $_.FullName, $rel) | Out-Null
}
$arc.Dispose(); $fs.Dispose()
Remove-Item $stage -Recurse -Force

$size = [math]::Round((Get-Item $zip).Length / 1MB, 2)
Write-Host ""
Write-Host "Готово: $zip ($copied файлов, $size МБ)" -ForegroundColor Green
Write-Host ""
Write-Host "Чек-лист перед отправкой:" -ForegroundColor Yellow
Write-Host "  1. .env в архиве нет — на сервере: cp .env.example .env и заполнить."
Write-Host "  2. SECRET_KEY и POSTGRES_PASSWORD сгенерировать: openssl rand -hex 32"
Write-Host "  3. Сервер без HTTPS -> в .env поставить COOKIE_SECURE=false."
Write-Host "  4. Ручной старт на сервере: docker compose up -d --build"
Write-Host "     Подробности: docs/06_деплой_на_сервер.md"
