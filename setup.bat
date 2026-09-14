@echo off
rem ReelForge - instalar dependencias (Windows).
rem
rem Doble click y listo: crea el entorno virtual en venv\ e instala ahi todo
rem lo de requirements.txt. No arranca la app ni descarga el modelo de
rem subtitulos (eso pasa solo la primera vez que se transcribe).
rem
rem Se puede volver a correr cuando se quiera: si el venv ya existe lo
rem reutiliza y solo actualiza los paquetes.
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title ReelForge - instalar dependencias

echo.
echo   ReelForge - instalar dependencias
echo   =================================
echo.

rem ---------------------------------------------------------------- Python --
rem El lanzador "py" es lo que deja el instalador de python.org; si no esta,
rem se prueba con "python" del PATH.
set "PY="
py -3 --version >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  python --version >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo   [FALTA] Python no esta instalado ^(o no quedo en el PATH^).
  echo.
  rem Python es lo unico que tiene que estar SI o SI antes: sin el no se puede
  rem crear el entorno virtual. Si hay winget, se ofrece instalarlo aca mismo.
  where winget >nul 2>&1
  if errorlevel 1 (
    echo   Instalalo desde python.org, tildando "Add python.exe to PATH",
    echo   y volve a correr este archivo.
    goto :fin
  )
  choice /c SN /n /m "   Instalar Python ahora con winget? [S/N] "
  if !errorlevel! equ 1 (
    echo.
    winget install --id Python.Python.3.13 -e --accept-package-agreements --accept-source-agreements
    echo.
    echo   Ahora CERRA esta ventana y hace doble click en setup.bat de nuevo:
    echo   hace falta una ventana nueva para que el PATH tome Python.
  ) else (
    echo.
    echo   Cuando quieras:  winget install Python.Python.3.13
  )
  goto :fin
)

%PY% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 (
  echo   [FALTA] Tu Python es muy viejo: hace falta 3.11 o mas nuevo.
  %PY% --version
  echo.
  goto :fin
)
for /f "tokens=2" %%v in ('%PY% --version 2^>^&1') do set "PYVER=%%v"
echo   [OK] Python !PYVER!

rem ------------------------------------------------------------------ venv --
if exist "venv\Scripts\python.exe" (
  echo   [OK] El entorno virtual ya existe, se reutiliza.
) else (
  echo   [..] Creando el entorno virtual en venv\ ...
  %PY% -m venv venv
  if errorlevel 1 (
    echo.
    echo   [ERROR] No se pudo crear el entorno virtual.
    goto :fin
  )
  echo   [OK] Entorno virtual creado.
)

rem ----------------------------------------------------------- dependencias --
echo.
echo   [..] Instalando dependencias ^(Flask, yt-dlp, faster-whisper, CUDA^).
echo        La primera vez baja ~2 GB y tarda un rato. Paciencia.
echo.
venv\Scripts\python.exe -m pip install --upgrade pip --quiet --disable-pip-version-check
venv\Scripts\python.exe -m pip install -r requirements.txt --disable-pip-version-check
if errorlevel 1 (
  echo.
  echo   [ERROR] Fallo la instalacion de dependencias ^(ver el detalle arriba^).
  goto :fin
)
echo.
echo   [OK] Dependencias instaladas.

rem ------------------------------------------------- programas del sistema --
rem Estos NO viven en el venv: son binarios del sistema.
echo.
echo   Programas del sistema:
set "FALTAN="
call :chequear ffmpeg "FFmpeg - procesa todo el video, obligatorio" Gyan.FFmpeg
call :chequear ffprobe "ffprobe - viene con FFmpeg" Gyan.FFmpeg
call :chequear node "Node - solo para bajar de YouTube" OpenJS.NodeJS.LTS
call :chequear git "Git - para actualizar y para la vista Historial" Git.Git

if defined FALTAN (
  echo.
  where winget >nul 2>&1
  if errorlevel 1 (
    echo   Instalalos a mano ^(los comandos estan arriba^) y volve a correr esto.
  ) else (
    choice /c SN /n /m "   Instalar lo que falta con winget ahora? [S/N] "
    if !errorlevel! equ 1 (
      echo.
      for %%p in (!FALTAN!) do (
        echo   [..] winget install %%p
        winget install --id %%p -e --accept-package-agreements --accept-source-agreements
      )
      echo.
      echo   Ojo: abri una ventana NUEVA para que el PATH tome los programas nuevos.
    )
  )
)

rem ------------------------------------------------------------------ fuente --
dir /b assets\fonts\*.ttf assets\fonts\*.otf >nul 2>&1
if errorlevel 1 (
  echo.
  echo   [AVISO] No hay ninguna fuente en assets\fonts\: los textos y los
  echo           subtitulos van a fallar. Copia ahi un .ttf o .otf.
)

echo.
echo   ===================================================================
echo    Listo. Para usar la app:  venv\Scripts\python.exe app.py
echo    y abri  http://127.0.0.1:5001  en el navegador.
echo   ===================================================================

:fin
echo.
pause
exit /b

rem --------------------------------------------------------------------------
rem :chequear <comando> <descripcion> <id de winget>
rem Marca el programa como faltante y deja a mano el comando para instalarlo.
:chequear
where %1 >nul 2>&1
if errorlevel 1 (
  echo   [FALTA] %~2
  echo           winget install %3
  echo !FALTAN! | findstr /i /c:"%3" >nul
  if errorlevel 1 set "FALTAN=!FALTAN! %3"
) else (
  echo   [OK] %~2
)
exit /b
