@echo off
rem ReelForge - arrancar la app (Windows).
rem
rem Doble click: levanta el servidor y abre el navegador solo. Esta ventana es
rem el servidor: mientras siga abierta la app funciona; cerrala (o Ctrl+C) para
rem apagarla.
rem
rem Si todavia no instalaste las dependencias, corre antes setup.bat.
chcp 65001 >nul
cd /d "%~dp0"

rem Modo interno: este mismo .bat, relanzado en segundo plano, es el que espera
rem a que el servidor levante para recien ahi abrir el navegador.
if /i "%~1"=="--abrir" goto :abrir

title ReelForge - servidor

set "URL=http://127.0.0.1:5001"

echo.
echo   ReelForge
echo   =========
echo.

if not exist "venv\Scripts\python.exe" (
  echo   [FALTA] No hay entorno virtual en venv\.
  echo.
  echo   Corre primero setup.bat ^(doble click^) para instalar las dependencias.
  echo.
  pause
  exit /b
)

rem Si ya hay una instancia corriendo, no se levanta otra: se abre y listo.
call :responde
if not errorlevel 1 (
  echo   Ya habia una instancia corriendo. Abriendo el navegador...
  start "" "%URL%"
  echo.
  echo   ^(esta ventana se puede cerrar: el servidor es la otra^)
  echo.
  pause
  exit /b
)

echo   Levantando el servidor... el navegador se abre solo en unos segundos.
echo   Para apagarlo: cerra esta ventana o Ctrl+C.
echo.

start "" /b cmd /c ""%~f0" --abrir"
venv\Scripts\python.exe app.py

echo.
echo   El servidor se detuvo.
pause
exit /b

rem --------------------------------------------------------------------------
rem Espera a que el servidor conteste y abre el navegador. Corre en paralelo
rem al servidor, que se queda con la ventana.
:abrir
set "URL=http://127.0.0.1:5001"
where curl >nul 2>&1
if errorlevel 1 (
  rem Sin curl no hay a quien preguntarle: se espera un rato fijo y se abre.
  ping -n 6 127.0.0.1 >nul
  start "" "%URL%"
  exit /b
)
for /l %%i in (1,1,60) do (
  call :responde
  if not errorlevel 1 (
    start "" "%URL%"
    exit /b
  )
  ping -n 2 127.0.0.1 >nul
)
exit /b

rem --------------------------------------------------------------------------
rem errorlevel 0 si el servidor contesta en %URL%. Sin curl no se puede saber:
rem se contesta que no y cada quien decide (arriba se espera a ciegas).
:responde
where curl >nul 2>&1
if errorlevel 1 exit /b 1
curl -s -o NUL --max-time 2 "%URL%" >nul 2>&1
exit /b %errorlevel%
