@echo off
setlocal EnableExtensions
cd /d "%~dp0"

for %%F in (nettraffictray.py speed_window.py performance_window.py app_icons.py) do (
if not exist "%%F" (
echo Missing file: %%F
echo Put all .py files in the same folder as this .bat.
goto :fail
)
)

set "PY=python"
where python >nul 2>&1
if errorlevel 1 (
where py >nul 2>&1
if errorlevel 1 (
echo Python was not found.
echo Install Python 3 from https://www.python.org/downloads/
echo and check "Add python.exe to PATH".
pause
exit /b 1
)
set "PY=py -3"
)

echo Installing pystray, psutil, pillow, and pyinstaller...
%PY% -m pip install --upgrade pystray psutil pillow pyinstaller
if errorlevel 1 goto :fail

echo Writing nettraffictray.ico ...
%PY% -c "import nettraffictray; nettraffictray.save_app_icon('nettraffictray.ico')"
if errorlevel 1 goto :fail

echo Building dist\NetTrafficTray.exe ...
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed --name NetTrafficTray --icon nettraffictray.ico --add-data "nettraffictray.ico;." --collect-submodules pystray --hidden-import pystray._win32 --hidden-import pystray._util.win32 --hidden-import six --hidden-import PIL.Image --hidden-import PIL.ImageDraw --hidden-import speed_window --hidden-import performance_window --hidden-import app_icons nettraffictray.py
if errorlevel 1 goto :fail

echo.
echo Built: %cd%\dist\NetTrafficTray.exe
echo.
echo Run that file. The tray icon is static (nettraffictray.ico); hover it to see
echo the live speed. Left-click toggles the meter. Right-click opens settings.
pause
exit /b 0

:fail
echo.
echo Build failed. See the messages above.
pause
exit /b 1