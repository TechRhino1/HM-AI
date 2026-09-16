@echo off
title HM Algo 2.0 Dashboard (UI + REST API only)
cd /d "%~dp0"
echo ======================================================================
echo    Starting HM Algo 2.0 Dashboard  ^(UI + REST API only^)
echo ======================================================================
echo.
echo  This starts the web terminal on http://127.0.0.1:8501 and NOTHING else.
echo  No trading engine, no MT5 client, no public tunnel.
echo.
echo  Use HM_start.bat instead if you want the autonomous trading engine and
echo  the remote-access tunnel as well.
echo.
echo  Keep this window open - closing it stops the dashboard.
echo ======================================================================
echo.
python -c "import sys; sys.path.insert(0, '.'); from jarvis.api.server import start_server; s = start_server(host='127.0.0.1', port=8501, mt5_client=None, orchestrator=None); print('listening on http://127.0.0.1:8501', flush=True); s.serve_forever()"
echo.
echo Dashboard stopped.
pause
