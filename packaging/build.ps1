# Сборка claudeAnty: exe (PyInstaller) -> установщик (Inno Setup 6) -> контрольная сумма для автообновления.
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1
# Нужно: Python с зависимостями из requirements.txt, Inno Setup 6 (https://jrsoftware.org/isdl.php).
# Результат: build\release\claudeAnty-setup-<версия>.exe и .exe.sha256 — оба файла приложите к релизу GitHub
# с тегом v<версия> (gh release create v1.6.0 build\release\*), и установленные копии предложат обновиться.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$version = (Select-String -Path "app\__init__.py" -Pattern '__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
Write-Host "claudeAnty $version"

python -m pip install --upgrade "pyinstaller>=6.10"
python -m PyInstaller packaging\claudeAnty.spec --noconfirm --clean --distpath build\dist --workpath build\work
if ($LASTEXITCODE -ne 0) { throw "PyInstaller завершился с ошибкой" }

$iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
          "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Warning "Inno Setup 6 не найден — собран только build\dist\claudeAnty (портативная версия: положите рядом папку data)."
    exit 0
}
New-Item -ItemType Directory -Force build\release | Out-Null
& $iscc packaging\installer.iss "/DAppVersion=$version" "/DSourceDir=$root\build\dist\claudeAnty" "/O$root\build\release"
if ($LASTEXITCODE -ne 0) { throw "ISCC завершился с ошибкой" }

$setup = "build\release\claudeAnty-setup-$version.exe"
$hash = (Get-FileHash $setup -Algorithm SHA256).Hash.ToLower()
"$hash  claudeAnty-setup-$version.exe" | Out-File -Encoding ascii "$setup.sha256"
Write-Host "Готово: $setup`nSHA-256: $hash"
