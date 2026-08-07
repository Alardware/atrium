@echo off
rem Atrium — lancement en developpement (sans Docker)
set ATRIUM_CONFIG_DIR=%~dp0data
start "" http://localhost:8420
"D:\Apps\Python\python.exe" "%~dp0app\server.py"
