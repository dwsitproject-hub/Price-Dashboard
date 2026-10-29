@echo off
cd /d "C:\Users\Algo.Trader\Desktop\DataExploration\Code\LSEGDashboard"
"C:\Program Files\Python310\python.exe" update.py
if %ERRORLEVEL% neq 0 (
    echo Update failed - dashboard NOT restarted
    pause
    exit /b 1
)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8055" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a
)
timeout /t 3 /nobreak
start "" "C:\Program Files\Python310\pythonw.exe" "C:\Users\Algo.Trader\Desktop\DataExploration\Code\LSEGDashboard\scripts\dashboard.py"
