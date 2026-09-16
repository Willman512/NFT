:: Windows. Put a shortcut to this file in:
::   shell:startup   (press Win+R, paste that, hit enter)
:: The office then starts whenever you log in.
@echo off
cd /d "%~dp0.."
call .venv\Scripts\activate.bat
forge office --no-browser
