@echo off
rem SSDT Atlas: uruchamia wtyczke przez uv (Windows; na macOS / Linux bin/atlas).
rem
rem Wspolne srodowisko (%USERPROFILE%\.data-architect\venv) zawiera tylko zewnetrzne biblioteki z uv.lock
rem (uv dobiera tez Pythona). Kod wtyczki ladujemy wprost z jej katalogu (PYTHONPATH=<wtyczka>\src), wiec kilka
rem sesji i wersji wtyczki moze dzialac rownoczesnie bez przeinstalowywania pakietu (Windows blokuje uzywane pliki).
rem
rem   bin\atlas install   przygotowuje srodowisko (pobiera Pythona, jesli trzeba, i biblioteki); wola je /ssdt-atlas:setup
rem   bin\atlas [...]     uruchamia wtyczke (bez argumentow: serwer MCP)

setlocal
for %%R in ("%~dp0..") do set "ROOT=%%~fR"
set "ATLAS_PLUGIN_ROOT=%ROOT%"
set "PYTHONPATH=%ROOT%\src;%PYTHONPATH%"
if defined ATLAS_VENV (set "UV_PROJECT_ENVIRONMENT=%ATLAS_VENV%") else (set "UV_PROJECT_ENVIRONMENT=%USERPROFILE%\.data-architect\venv")

rem uv z opcjami wspolnymi: zablokowane wersje z uv.lock, bez narzedzi deweloperskich, bez usuwania pakietow,
rem ktorych nie zna ta wersja wtyczki (moga ich uzywac inne sesje)
set "UV_OPTIONS=--frozen --no-dev --inexact"

rem --- uv: z PATH albo z typowych miejsc instalacji (PATH sesji mogl nie zostac odswiezony po instalacji) ---
set "UV=%ATLAS_UV%"
if not defined UV for %%X in (uv.exe) do set "UV=%%~$PATH:X"
if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV=%USERPROFILE%\.cargo\bin\uv.exe"
if not defined UV goto no_uv

rem --- przygotowanie srodowiska ---
if "%~1"=="install" goto install
if not exist "%UV_PROJECT_ENVIRONMENT%" goto no_env

"%UV%" run --project "%ROOT%" %UV_OPTIONS% --quiet python -m data_architect %*
exit /b %ERRORLEVEL%

:install
"%UV%" sync --project "%ROOT%" %UV_OPTIONS%
exit /b %ERRORLEVEL%

:no_uv
echo SSDT Atlas: brak uv (menedzer Pythona dla wtyczki). Zaproponuj uzytkownikowi /ssdt-atlas:setup.
if "%~1"=="check" exit /b 0
exit /b 1

:no_env
echo SSDT Atlas: srodowisko wtyczki nie jest przygotowane. Zaproponuj uzytkownikowi /ssdt-atlas:setup.
if "%~1"=="check" exit /b 0
exit /b 1
