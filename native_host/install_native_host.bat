@echo off
setlocal
REM ============================================================================
REM Instalador do Chrome Native Messaging Host (apenas HKCU — SEM admin!)
REM Usa `run_host.bat` + Python assinado da `.venv` (100%% compativel com
REM Windows Smart App Control — nunca bloqueado por WinError 4551!).
REM Uso: install_native_host.bat [ID_DA_EXTENSAO_CHROME]
REM ============================================================================

set "HOST_DIR=%~dp0"
set "ROOT_DIR=%HOST_DIR%..\"
set "PYTHON_EXE=%ROOT_DIR%.venv\Scripts\python.exe"
set "MANIFEST_OUT=%HOST_DIR%com.scanlatorvogels.host.json"
set "RUN_HOST_BAT=%HOST_DIR%run_host.bat"
set "REG_KEY=HKCU\Software\Google\Chrome\NativeMessagingHosts\com.scanlatorvogels.host"

set "EXT_ID=%~1"
if "%EXT_ID%"=="" (
    echo.
    echo Abra chrome://extensions, carregue "extension\dist" e copie o ID de 32 letras.
    set /p "EXT_ID=Cole o ID da extensao Chrome aqui: "
)

if "%EXT_ID%"=="" (
    echo [ERRO] ID da extensao nao informado. Cancelado.
    exit /b 1
)

if not exist "%PYTHON_EXE%" (
    echo [ERRO] Python .venv nao encontrado em "%PYTHON_EXE%".
    exit /b 1
)

"%PYTHON_EXE%" -c "import json, pathlib, sys; ext_id = sys.argv[1].strip(); target = str(pathlib.Path(sys.argv[2]).resolve()); out = pathlib.Path(sys.argv[3]); manifest = {'name': 'com.scanlatorvogels.host', 'description': 'Scanlator Vogels PT-BR On-Demand Local Engine Controller', 'path': target, 'type': 'stdio', 'allowed_origins': [f'chrome-extension://{ext_id}/']}; out.write_text(json.dumps(manifest, indent=2), encoding='utf-8'); print('Manifest salvo em:', out)" "%EXT_ID%" "%RUN_HOST_BAT%" "%MANIFEST_OUT%"
if errorlevel 1 (
    echo [ERRO] Falha ao gerar o manifesto Native Messaging.
    exit /b 1
)

reg add "%REG_KEY%" /ve /t REG_SZ /d "%MANIFEST_OUT%" /f >nul
if errorlevel 1 (
    echo [ERRO] Falha ao gravar chave HKCU no registro.
    exit /b 1
)

echo.
echo [OK] Native Messaging Host registrado em HKCU (sem admin, compativel com Smart App Control)!
echo      Chave:     %REG_KEY%
echo      Alvo:      %RUN_HOST_BAT%
echo      Manifesto: %MANIFEST_OUT%
echo      Extensao:  chrome-extension://%EXT_ID%/
endlocal
