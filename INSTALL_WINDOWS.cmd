@echo off
setlocal
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONUTF8=1"
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 11)" >nul 2>nul
if %ERRORLEVEL% EQU 0 goto use_python
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 11)" >nul 2>nul
if %ERRORLEVEL% EQU 0 goto use_py
set "PYTHONEXE="
for /d %%D in ("%LocalAppData%\Programs\Python\Python3*") do if exist "%%~fD\python.exe" set "PYTHONEXE=%%~fD\python.exe"
if defined PYTHONEXE goto use_exe
echo Python 3.10 or newer is required. Install it from python.org and retry. No download was attempted.
set "RESULT=10"
goto finish
:use_python
python -B "%~dp0install.py" %*
set "RESULT=%ERRORLEVEL%"
goto finish
:use_py
py -3 -B "%~dp0install.py" %*
set "RESULT=%ERRORLEVEL%"
goto finish
:use_exe
"%PYTHONEXE%" -B "%~dp0install.py" %*
set "RESULT=%ERRORLEVEL%"
:finish
if not "%RESULT%"=="0" if "%~1"=="" pause
exit /b %RESULT%
