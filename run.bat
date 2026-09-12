@echo off
cd /d "%~dp0src"
uv run flask --app app run --debug --no-reload
