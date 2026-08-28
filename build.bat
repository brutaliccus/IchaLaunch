@echo off
cd /d "%~dp0"
python -m pip install -r requirements.txt
python -m PyInstaller IchaLaunch.spec --noconfirm
echo.
echo Built: dist\IchaLaunch.exe
echo.
echo After a release build, sign locally (never CI) and upload BOTH files:
echo   python tools\sign.py --key "%LOCALAPPDATA%\IchaLaunch\signing\ichalaunch-key1.pem" dist\IchaLaunch.exe
echo   then attach dist\IchaLaunch.exe and dist\IchaLaunch.exe.sig to the GitHub release.
pause
