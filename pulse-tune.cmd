@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tune.ps1" %*
