@echo off
setlocal EnableExtensions DisableDelayedExpansion
set "packageRoot=%~dp0"
set "runtimeExe=%packageRoot%codex-plugin\runtime\between-the-lines-plugin\between-the-lines-plugin.exe"

if not exist "%runtimeExe%" (
    echo The plugin runtime is missing. Extract the complete release ZIP first.
    if /I not "%~1"=="--prepare-only" pause
    exit /b 1
)

"%runtimeExe%" --prepare-install
if errorlevel 1 (
    echo between-the-lines could not prepare this package. No marketplace was added.
    if /I not "%~1"=="--prepare-only" pause
    exit /b 1
)
if /I "%~1"=="--prepare-only" exit /b 0

where codex >nul 2>nul
if errorlevel 1 (
    echo The package is prepared. Install a current Codex CLI, then run:
    echo codex plugin marketplace add "%packageRoot%."
    echo Open the desktop Plugins Directory afterward and install between-the-lines.
    pause
    exit /b 2
)

call codex plugin marketplace add "%packageRoot%."
if errorlevel 1 (
    echo The package is prepared, but marketplace registration failed.
    echo A current Codex CLI with plugin marketplace support is required.
    echo After updating it, run: codex plugin marketplace add "%packageRoot%."
    pause
    exit /b 1
)

echo Marketplace added. Open the Codex desktop Plugins Directory and install between-the-lines.
echo Then review and trust its four hooks in /hooks before using automatic annotations.
pause
exit /b 0
