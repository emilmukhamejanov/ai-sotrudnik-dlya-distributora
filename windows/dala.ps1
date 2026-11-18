# Запуск проекта на Windows без make и WSL.
#   setup — Python-окружение, зависимости, .env с паролями базы
#   start — база в Docker, индексация документов, API на http://127.0.0.1:8000
#   stop  — остановить базу;  test — тесты;  reset — пересоздать базу с нуля
#   evals — прогон эталонных вопросов на настоящей модели (тратит токены), отчёт в var\evals
#   ui    — пересобрать дашборд (нужен Node.js 20.19+); в архиве проекта он уже собран
#   docker-up / docker-down — всё целиком в Docker (база, агент, дашборд), без Python на компьютере
param(
    [ValidateSet('setup', 'start', 'stop', 'test', 'reset', 'evals', 'ui', 'docker-up', 'docker-down')]
    [string]$Command = 'start'
)

$ErrorActionPreference = 'Continue'  # ошибки внешних команд проверяем по коду выхода
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$VenvPy = Join-Path $Root '.venv\Scripts\python.exe'
$EnvFile = Join-Path $Root '.env'
$DockerUrl = 'https://www.docker.com/products/docker-desktop/'

function Say([string]$Text, [string]$Color = 'Cyan') { Write-Host $Text -ForegroundColor $Color }
function Ok([string]$Text) { Write-Host "  [ok] $Text" -ForegroundColor Green }
function Fail([string]$Text) {
    Write-Host ''
    Write-Host $Text -ForegroundColor Red
    exit 1
}

function Find-Python {
    foreach ($v in '3.13', '3.12') {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            $exe = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) { return "$exe".Trim() }
        }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $ver = & python -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $ver -and ([version]"$ver".Trim() -ge [version]'3.12')) {
            return (& python -c "import sys; print(sys.executable)").Trim()
        }
    }
    return $null
}

function New-Secret {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return ([Convert]::ToBase64String($bytes) -replace '[^A-Za-z0-9]', '').Substring(0, 24)
}

function Write-Utf8NoBom([string]$Path, [string]$Text) {
    [IO.File]::WriteAllText($Path, $Text, (New-Object Text.UTF8Encoding $false))
}

function Get-EnvValue([string]$Name) {
    if (-not (Test-Path $EnvFile)) { return '' }
    foreach ($line in Get-Content $EnvFile -Encoding UTF8) {
        if ($line -match "^\s*$Name\s*=(.*)$") { return $Matches[1].Trim().Trim('"').Trim("'") }
    }
    return ''
}

function Ensure-EnvFile {
    if (Test-Path $EnvFile) { Ok '.env уже есть — не трогаю'; return }
    $text = Get-Content (Join-Path $Root '.env.example') -Raw -Encoding UTF8
    $text = $text -replace '(?m)^POSTGRES_PASSWORD=[ \t]*\r?$', ('POSTGRES_PASSWORD=' + (New-Secret))
    $text = $text -replace '(?m)^AGENT_DB_PASSWORD=[ \t]*\r?$', ('AGENT_DB_PASSWORD=' + (New-Secret))
    Write-Utf8NoBom $EnvFile $text
    Ok '.env создан, пароли базы сгенерированы автоматически'
}

function Test-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return 'missing' }
    cmd /c "docker info >nul 2>&1"
    if ($LASTEXITCODE -eq 0) { return 'running' }
    return 'stopped'
}

function Ensure-DockerRunning {
    $state = Test-Docker
    if ($state -eq 'missing') {
        Fail "Docker Desktop не установлен. Скачайте и установите: $DockerUrl`nПосле установки перезагрузите компьютер, запустите Docker Desktop и снова откройте start-windows.bat."
    }
    if ($state -eq 'running') { Ok 'Docker работает'; return }
    $exe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (Test-Path $exe) {
        Say 'Запускаю Docker Desktop (до 2 минут)…'
        Start-Process $exe
    } else {
        Say 'Запустите Docker Desktop вручную — жду до 2 минут…' 'Yellow'
    }
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 2
        if ((Test-Docker) -eq 'running') { Ok 'Docker работает'; return }
    }
    Fail 'Docker Desktop так и не запустился. Откройте его вручную, дождитесь зелёного статуса «Engine running» и повторите.'
}

function Ensure-Ui([switch]$Force) {
    $index = Join-Path $Root 'frontend\dist\index.html'
    if ((Test-Path $index) -and -not $Force) { Ok 'Дашборд собран'; return }
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Say '  [!] Дашборд не собран, а Node.js не найден — будет доступен только API (/docs)' 'Yellow'
        return
    }
    Say 'Собираю дашборд (npm)…'
    Push-Location (Join-Path $Root 'frontend')
    npm ci --no-audit --no-fund
    if ($LASTEXITCODE -eq 0) { npm run build }
    $code = $LASTEXITCODE
    Pop-Location
    if ($code -ne 0) { Say '  [!] Сборка дашборда не удалась — API всё равно работает (/docs)' 'Yellow' } else { Ok 'Дашборд собран' }
}

function Invoke-Setup {
    Say "`n=== Установка ===" 'White'
    $py = Find-Python
    if (-not $py) { Fail 'Не найден Python 3.12+. Установите с https://www.python.org/downloads/ (галочка «Add python.exe to PATH»).' }
    Ok "Python: $py"

    if (-not (Test-Path $VenvPy)) {
        Say 'Создаю виртуальное окружение .venv…'
        & $py -m venv .venv
        if ($LASTEXITCODE -ne 0) { Fail 'Не удалось создать .venv' }
    }
    Ok 'Окружение .venv'

    Say 'Ставлю зависимости (первый раз 2–5 минут)…'
    & $VenvPy -m pip install --disable-pip-version-check -q --upgrade pip
    & $VenvPy -m pip install --disable-pip-version-check -q -e 'backend[dev]'
    if ($LASTEXITCODE -ne 0) { Fail 'pip не смог установить зависимости — смотрите сообщение выше.' }
    Ok 'Зависимости установлены'

    Ensure-EnvFile
    Ensure-Ui

    switch (Test-Docker) {
        'missing' { Say "  [!] Docker Desktop не установлен — он нужен для базы: $DockerUrl" 'Yellow' }
        'stopped' { Say '  [!] Docker Desktop установлен, но не запущен — start-windows.bat запустит его сам' 'Yellow' }
        default   { Ok 'Docker работает' }
    }

    if (-not (Get-EnvValue 'ANTHROPIC_API_KEY')) {
        Say "`nОсталось вписать ключ: строка ANTHROPIC_API_KEY= в файле .env (открываю его в Блокноте)." 'Yellow'
        Say 'Сохраните файл (Ctrl+S), затем запустите start-windows.bat.' 'Yellow'
        Start-Process notepad.exe $EnvFile
    } else {
        Say "`nГотово. Теперь запустите start-windows.bat." 'Green'
    }
}

function Invoke-Start {
    if (-not (Test-Path $VenvPy)) { Invoke-Setup }
    if (-not (Test-Path $EnvFile)) { Ensure-EnvFile }
    Say "`n=== Запуск ===" 'White'

    $provider = Get-EnvValue 'LLM_PROVIDER'
    if ((-not $provider -or $provider -eq 'anthropic') -and -not (Get-EnvValue 'ANTHROPIC_API_KEY')) {
        Start-Process notepad.exe $EnvFile
        Fail 'В .env не заполнен ANTHROPIC_API_KEY. Вставьте ключ в открытом Блокноте, сохраните и запустите снова.'
    }

    Ensure-DockerRunning
    Say 'Поднимаю базу Postgres + pgvector (первый раз скачивается образ и грузятся демо-данные)…'
    docker compose up -d --wait db
    if ($LASTEXITCODE -ne 0) {
        Fail "База не запустилась. Частая причина — порт 5432 занят другим Postgres. Подробности: docker compose logs db"
    }
    Ok 'База работает'

    Say 'Индексирую документы в базу знаний…'
    & $VenvPy -m dala_agent.rag.indexer
    if ($LASTEXITCODE -ne 0) { Fail 'Индексация не удалась — смотрите сообщение выше.' }

    $hasUi = Test-Path (Join-Path $Root 'frontend\dist\index.html')
    $url = if ($hasUi) { 'http://127.0.0.1:8000/' } else { 'http://127.0.0.1:8000/docs' }
    Say "`nДашборд: $url  (API — /docs). Откроется в браузере. Остановить: Ctrl+C в этом окне." 'Green'
    Start-Job -ScriptBlock { param($u) Start-Sleep -Seconds 5; Start-Process $u } -ArgumentList $url | Out-Null
    & $VenvPy -m uvicorn dala_agent.api.main:create_app --factory --host 127.0.0.1 --port 8000
}

function Invoke-Test {
    if (-not (Test-Path $VenvPy)) { Invoke-Setup }
    Push-Location (Join-Path $Root 'backend')
    & $VenvPy -m pytest -q
    $code = $LASTEXITCODE
    Pop-Location
    exit $code
}

switch ($Command) {
    'setup' { Invoke-Setup }
    'start' { Invoke-Start }
    'stop'  { docker compose stop; Ok 'База остановлена (данные сохранены)' }
    'test'  { Invoke-Test }
    'ui'    { Ensure-Ui -Force }
    'docker-up' {
        if (-not (Test-Path $EnvFile)) { Ensure-EnvFile }
        $provider = Get-EnvValue 'LLM_PROVIDER'
        if ((-not $provider -or $provider -eq 'anthropic') -and -not (Get-EnvValue 'ANTHROPIC_API_KEY')) {
            Start-Process notepad.exe $EnvFile
            Fail 'В .env не заполнен ANTHROPIC_API_KEY. Вставьте ключ в открытом Блокноте, сохраните и запустите снова.'
        }
        Ensure-DockerRunning
        Say 'Собираю образ и поднимаю базу, агента и дашборд (первый раз — несколько минут)…'
        docker compose up -d --build --wait
        if ($LASTEXITCODE -ne 0) {
            Fail 'Контейнеры не запустились. Если открыт start-windows.bat — закройте его (порт 8000 занят). Подробности: docker compose logs app'
        }
        Ok 'Работает в Docker'
        Say "`nДашборд: http://127.0.0.1:8000/  Остановить: windows\docker-down.bat" 'Green'
        Start-Process 'http://127.0.0.1:8000/'
    }
    'docker-down' { docker compose down; Ok 'Контейнеры остановлены (данные сохранены)' }
    'evals' {
        if (-not (Test-Path $VenvPy)) { Invoke-Setup }
        Ensure-DockerRunning
        docker compose up -d --wait db
        & $VenvPy -m dala_agent.rag.indexer
        & $VenvPy -m dala_agent.evals
    }
    'reset' {
        $answer = Read-Host 'Удалить базу со всеми данными и создать заново? (y — да, n — нет)'
        if ($answer -notin @('y', 'Y', 'да')) { Say 'Отменено'; exit 0 }
        Ensure-DockerRunning
        docker compose down -v
        docker compose up -d --wait db
        if ($LASTEXITCODE -eq 0) { Ok 'База пересоздана. Запустите start-windows.bat — документы проиндексируются заново.' }
    }
}
