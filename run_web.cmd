@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem Yinluxs campus assistant - web launcher (double-click to run).
rem Keep this file ASCII-only: cmd.exe parses .cmd with the local ANSI codepage,
rem so non-ASCII text here gets mangled into broken commands.
rem Uses the project venv interpreter to avoid the Microsoft Store python stub.

if not exist ".venv\Scripts\python.exe" goto novenv

echo Starting Yinluxs web server ... keep this window open (Ctrl+C stops it).
echo.
".venv\Scripts\python.exe" web_main.py %*
set RC=%ERRORLEVEL%
echo.
echo Server stopped (exit code %RC%).
pause
exit /b %RC%

:novenv
echo [x] .venv\Scripts\python.exe not found.
echo     Create the virtualenv first, from this folder:
echo         py -3.12 -m venv .venv
echo         .venv\Scripts\pip install -r requirements.txt
pause
exit /b 1
