@echo off
title ReelForge
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] No se encontro el entorno virtual en venv\
    echo Crealo con:  python -m venv venv  y luego  venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

echo Iniciando ReelForge en http://127.0.0.1:5001 ...
start "" http://127.0.0.1:5001
venv\Scripts\python.exe app.py

echo.
echo ReelForge se detuvo. Si hubo un error, aparece arriba.
pause
