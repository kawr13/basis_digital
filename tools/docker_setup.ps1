#Requires -Version 5.1
<#
.docker_setup.ps1 — подготовка Windows под Docker Desktop (WSL2-бэкенд).

Диагноз, под который писали:
  * Docker Desktop установлен, но демон не стартует —
    «WSL2 не поддерживается в текущей конфигурации компьютера»;
  * виртуализация отключена в BIOS (Win32_Processor.VMMonitorModeExtensions = False)
    — это ЕДИНСТВЕННОЕ, что не сделать скриптом: нужен перезапуск и BIOS;
  * компонент «Платформа виртуальных машин» (VirtualMachinePlatform)
    не включён — включаем здесь.

Запуск (ОБЯЗАТЕЛЬНО из административной PowerShell):
    cd D:\basis\statcab
    powershell -ExecutionPolicy Bypass -File .\tools\docker_setup.ps1

После скрипта: перезагрузка → BIOS: включить Virtualization Technology (VT-x/AMD-V)
→ перезагрузка → запустить Docker Desktop → docker run hello-world.
Подробности и проверки — в docs/07_docker_на_рабочей_машине.md.
#>
[CmdletBinding()]
param(
    # Не трогать WSL, только включить компоненты (если BIOS уже включён)
    [switch]$FeaturesOnly
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

# ── 0. Права администратора ────────────────────────────────────────────────
$me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Нужны права администратора: запустите PowerShell «от имени администратора»" -ForegroundColor Red
    exit 1
}

# ── 1. Текущее состояние, до ───────────────────────────────────────────────
Step "Состояние ДО"
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
Write-Host ("  CPU: " + $cpu.Name.Trim())
Write-Host ("  Виртуализация в BIOS (VMMonitorModeExtensions): " + $cpu.VMMonitorModeExtensions)
if (-not $cpu.VMMonitorModeExtensions) {
    Write-Host "  ВНИМАНИЕ: виртуализация выключена в BIOS. Скрипт включит компоненты," -ForegroundColor Yellow
    Write-Host "  но Docker заработает только после: перезагрузка → BIOS → VT-x/AMD-V → перезагрузка." -ForegroundColor Yellow
}
foreach ($f in @("VirtualMachinePlatform", "HypervisorPlatform")) {
    $st = (Get-WindowsOptionalFeature -Online -FeatureName $f).State
    Write-Host ("  Компонент " + $f + ": " + $st)
}

# ── 2. Включаем компоненты (без перезагрузки — скажем в конце) ─────────────
Step "Включение «Платформы виртуальных машин» и «Платформы гипервизора»"
foreach ($f in @("VirtualMachinePlatform", "HypervisorPlatform")) {
    $st = (Get-WindowsOptionalFeature -Online -FeatureName $f).State
    if ($st -eq "Enabled") { Write-Host "  $f — уже включён"; continue }
    Write-Host "  Включаю $f ..."
    Enable-WindowsOptionalFeature -Online -FeatureName $f -NoRestart | Out-Null
    Write-Host ("  ${f}: " + (Get-WindowsOptionalFeature -Online -FeatureName $f).State)
}

if ($FeaturesOnly) {
    Step "Готово (режим -FeaturesOnly): перезагрузитесь, затем включите виртуализацию в BIOS"
    exit 0
}

# ── 3. WSL2 по умолчанию (для Docker Desktop) ──────────────────────────────
Step "WSL: версия 2 по умолчанию"
try {
    wsl --set-default-version 2 2>&1 | ForEach-Object { Write-Host "  $_" }
} catch {
    Write-Host "  wsl --set-default-version не прошёл: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "  Если WSL не установлен вовсе — Docker Desktop установит его при первом старте." -ForegroundColor Yellow
}

# ── 4. Docker Desktop на месте? ────────────────────────────────────────────
Step "Docker Desktop"
$dd = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
if (Test-Path $dd) {
    Write-Host "  Найден: $dd"
} else {
    Write-Host "  НЕ найден. Установите Docker Desktop (desktop.docker.com) —" -ForegroundColor Red
    Write-Host "  после этого повторите этот скрипт и запустите Docker Desktop." -ForegroundColor Red
}

# ── 5. Итог ────────────────────────────────────────────────────────────────
Step "Что дальше"
Write-Host @"
  1. ПЕРЕЗАГРУЗИТЬ компьютер.
  2. В BIOS включить виртуализацию: Intel — «Intel Virtualization Technology»
     (VT-x), AMD — «SVM Mode». Обычно в разделе CPU / Advanced.
  3. После включения в BIOS — ещё раз перезагрузиться.
  4. Запустить Docker Desktop (пиктограмма кита), дождаться «Docker Desktop is running».
  5. Проверить:
       docker run --rm hello-world
       docker info | findstr /i "server version"
  6. Для бенчмарка P.12 (PostgreSQL):
       cd D:\basis\statcab
       powershell -ExecutionPolicy Bypass -File .\tools\pg_up.ps1
     — поднимет контейнер PostgreSQL 16 и напечатает DATABASE_URL для backend\.env.
"@
exit 0
