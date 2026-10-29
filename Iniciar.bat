@echo off
chcp 65001 >nul
title Radar de Noticias
cd /d "%~dp0"
echo Iniciando o Radar de Noticias...
python app.py
if errorlevel 1 (
  echo.
  echo Nao foi possivel iniciar. Verifique se o Python esta instalado: https://www.python.org/downloads/
  pause
)
