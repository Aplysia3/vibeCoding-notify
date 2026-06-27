@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "HOOK_SCRIPT=%SCRIPT_DIR%feishu_codex_hook.py"
set "PYTHONIOENCODING=utf-8"
set "PY_CMD="
set "PY_ARGS="

call :try_py
if defined PY_CMD goto run
call :try_python
if defined PY_CMD goto run
call :try_python3
if defined PY_CMD goto run

echo Python 3.10+ was not found. Install Python, then run this setup guide again.
echo Windows check command: py -3 --version
exit /b 1

:try_py
where py >nul 2>nul
if errorlevel 1 exit /b 0
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 exit /b 0
set "PY_CMD=py"
set "PY_ARGS=-3"
exit /b 0

:try_python
where python >nul 2>nul
if errorlevel 1 exit /b 0
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 exit /b 0
set "PY_CMD=python"
set "PY_ARGS="
exit /b 0

:try_python3
where python3 >nul 2>nul
if errorlevel 1 exit /b 0
python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 exit /b 0
set "PY_CMD=python3"
set "PY_ARGS="
exit /b 0

:run
if "%PY_ARGS%"=="" goto run_without_args
for /f "delims=" %%v in ('%PY_CMD% %PY_ARGS% -c "import sys; print(str(sys.version_info[0])+'.'+str(sys.version_info[1])+'.'+str(sys.version_info[2]))"') do set "PY_VERSION=%%v"
echo Python: OK %PY_VERSION% [%PY_CMD% %PY_ARGS%]
%PY_CMD% %PY_ARGS% "%HOOK_SCRIPT%" setup %*
exit /b %ERRORLEVEL%

:run_without_args
for /f "delims=" %%v in ('%PY_CMD% -c "import sys; print(str(sys.version_info[0])+'.'+str(sys.version_info[1])+'.'+str(sys.version_info[2]))"') do set "PY_VERSION=%%v"
echo Python: OK %PY_VERSION% [%PY_CMD%]
%PY_CMD% "%HOOK_SCRIPT%" setup %*
exit /b %ERRORLEVEL%
