@echo off
"%~dp0..\python\python.exe" "%~dp0py-launcher.py" %*
exit /b %errorlevel%
