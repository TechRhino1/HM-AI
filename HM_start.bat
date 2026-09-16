@echo off
title HM Algo 2.0 Trading Terminal
cd /d "%~dp0"
echo ======================================================================
echo           Starting HM Algo 2.0 Live Trading Terminal
echo ======================================================================
python HM_start.py %*
pause
