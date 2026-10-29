@echo off
echo Creating LSEGDashboard scheduled tasks...

REM Task 1: Daily data update at 08:00 Mon-Fri
schtasks /create /tn "LSEGDashboard_Update" ^
  /tr "\"C:\Users\Algo.Trader\Desktop\DataExploration\Code\LSEGDashboard\update.bat\"" ^
  /sc WEEKLY /d MON,TUE,WED,THU,FRI /st 08:00 ^
  /sd 01/01/2025 ^
  /f
if %errorlevel% neq 0 (
    echo FAILED to create LSEGDashboard_Update
    exit /b 1
)
echo Created: LSEGDashboard_Update

REM Task 2: Dashboard auto-start on user logon
schtasks /create /tn "LSEGDashboard_Start" ^
  /tr "\"C:\Program Files\Python310\pythonw.exe\" \"C:\Users\Algo.Trader\Desktop\DataExploration\Code\LSEGDashboard\scripts\dashboard.py\"" ^
  /sc ONLOGON ^
  /f
if %errorlevel% neq 0 (
    echo FAILED to create LSEGDashboard_Start
    exit /b 1
)
echo Created: LSEGDashboard_Start

echo.
echo Done. Verifying tasks...
echo.
schtasks /query /tn "LSEGDashboard_Update" /fo LIST
echo.
schtasks /query /tn "LSEGDashboard_Start" /fo LIST
