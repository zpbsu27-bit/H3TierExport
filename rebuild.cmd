@echo off
setlocal
cd /d "%~dp0"
set "PY=%~dp0venv-win\Scripts\python.exe"
if not exist "%PY%" set "PY=%~dp0build\creature-auto-pose-provider\venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if not exist "%PY%" set "PY=C:\msys64\ucrt64\bin\python.exe"
if not exist "%PY%" set "PY=python.exe"
echo [1/4] Regenerate icon...
"%PY%" -B -X utf8 scripts\make_icon.py || exit /b 1
echo [2/4] Build x64...
"%PY%" -B -X utf8 scripts\build.py || exit /b 1
echo [3/4] Run regression tests...
"%PY%" -B -X utf8 tests\run_tests.py || exit /b 1
echo [4/4] Publish verified release...
"%PY%" -B -X utf8 scripts\publish.py || exit /b 1
echo.
echo H3TierExport build/test/publish completed.
echo dist\H3TierExport.exe
endlocal
