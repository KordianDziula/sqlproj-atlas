@echo off
rem SqlProj Atlas: uruchamia wtyczke przez uv (Windows; na macOS / Linux scripts/atlas).
rem
rem Wspolne srodowisko (%USERPROFILE%\.sqlproj-atlas\venv) zawiera tylko zewnetrzne biblioteki z uv.lock
rem (uv dobiera tez Pythona). Kod wtyczki ladujemy wprost z jej katalogu (PYTHONPATH=<wtyczka>\src), wiec kilka
rem sesji i wersji wtyczki moze dzialac rownoczesnie bez przeinstalowywania pakietu (Windows blokuje uzywane pliki).
rem
rem   scripts\atlas install   przygotowuje srodowisko (pobiera Pythona, jesli trzeba, i biblioteki); wola je /sqlproj-atlas:setup
rem   scripts\atlas [...]     uruchamia wtyczke (bez argumentow: serwer MCP)
rem
rem uv wywolujemy zawsze po nazwie, z pelna komenda (walidator katalogu Anthropic musi widziec, co jest uruchamiane).
rem Opcje: --frozen (wersje z uv.lock), --no-dev (bez narzedzi deweloperskich), --inexact (bez usuwania pakietow,
rem ktorych nie zna ta wersja wtyczki, bo moga ich uzywac inne sesje).

setlocal
for %%R in ("%~dp0..") do set "ROOT=%%~fR"
set "ATLAS_PLUGIN_ROOT=%ROOT%"
set "PYTHONPATH=%ROOT%\src;%PYTHONPATH%"
if defined ATLAS_VENV (set "UV_PROJECT_ENVIRONMENT=%ATLAS_VENV%") else (set "UV_PROJECT_ENVIRONMENT=%USERPROFILE%\.sqlproj-atlas\venv")

rem --- uv: ATLAS_UV, potem PATH, potem typowe miejsca instalacji (PATH sesji mogl nie zostac odswiezony po instalacji) ---
set "PATH=%PATH%;%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin"
if defined ATLAS_UV for %%F in ("%ATLAS_UV%") do set "PATH=%%~dpF;%PATH%"
where uv >nul 2>nul || goto no_uv

rem --- przygotowanie srodowiska ---
if "%~1"=="install" goto install
if not exist "%UV_PROJECT_ENVIRONMENT%" goto no_env

uv run --project "%ROOT%" --frozen --no-dev --inexact --quiet python -m sqlproj_atlas %*
exit /b %ERRORLEVEL%

:install
uv sync --project "%ROOT%" --frozen --no-dev --inexact
exit /b %ERRORLEVEL%

:no_uv
echo SqlProj Atlas: brak uv (menedzer Pythona dla wtyczki). Zaproponuj uzytkownikowi /sqlproj-atlas:setup.
if "%~1"=="check" exit /b 0
exit /b 1

:no_env
echo SqlProj Atlas: srodowisko wtyczki nie jest przygotowane. Zaproponuj uzytkownikowi /sqlproj-atlas:setup.
if "%~1"=="check" exit /b 0
exit /b 1
