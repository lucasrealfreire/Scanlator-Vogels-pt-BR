@echo off
setlocal
REM ============================================================================
REM Scanlator Vogels PT-BR — Inicializador Sob Demanda do Motor Local (127.0.0.1)
REM NAO inicia com o Windows; encerra automaticamente apos 5 min de inatividade
REM ou imediatamente ao clicar em "Desligar Motor" na extensao.
REM ============================================================================

set "ROOT_DIR=%~dp0"
set "PYTHON_EXE=%ROOT_DIR%.venv\Scripts\python.exe"
set "LOG_FILE=%ROOT_DIR%native_host\backend.log"

if not exist "%PYTHON_EXE%" (
    echo [ERRO] Ambiente virtual nao encontrado em "%PYTHON_EXE%".
    exit /b 1
)

cd /d "%ROOT_DIR%"

REM Modo silencioso (sem janela de console — usa python.exe com CREATE_NO_WINDOW)
if /I "%~1"=="--silent" (
    shift
    "%PYTHON_EXE%" -c "import subprocess, sys; flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP; si = subprocess.STARTUPINFO(); si.dwFlags |= subprocess.STARTF_USESHOWWINDOW; si.wShowWindow = 0; fh = open(r'%LOG_FILE%', 'ab'); subprocess.Popen([r'%PYTHON_EXE%', '-m', 'backend.server', *sys.argv[1:]], cwd=r'%ROOT_DIR%', stdin=subprocess.DEVNULL, stdout=fh, stderr=fh, close_fds=True, creationflags=flags, startupinfo=si)" %1 %2 %3 %4 %5 %6
    exit /b 0
)

echo ============================================================================
echo   SCANLATOR VOGELS PT-BR — Motor Local Sob Demanda (http://127.0.0.1:8765)
echo   - Custo Zero: Gemini Free Tier + Visao 100%% local
echo   - Auto-desligamento por inatividade: 5 minutos (libera 100%% da VRAM)
echo   - Para encerrar agora: clique em "Desligar Motor" na extensao ou Ctrl+C
echo ============================================================================
"%PYTHON_EXE%" -m backend.server %*
endlocal
