@echo off
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
python "%~dp0pulse.py" %*
