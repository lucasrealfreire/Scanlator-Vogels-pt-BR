@echo off
setlocal
set "REG_KEY=HKCU\Software\Google\Chrome\NativeMessagingHosts\com.scanlatorvogels.host"
reg delete "%REG_KEY%" /f >nul 2>&1
echo [OK] Native Messaging Host removido de HKCU.
endlocal
