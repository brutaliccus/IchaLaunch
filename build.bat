@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt
python -m PyInstaller IchaLaunch.spec --noconfirm
echo.
echo Built: dist\IchaLaunch.exe
pause
