# Scanlator Vogels PT-BR — Etapa 2 (Motor Local Sob Demanda + Extensão Universal MV3)

Extensão universal **Manifest V3** + microserviço local sob demanda em `127.0.0.1:8765` + tradução natural estilo scanlation PT-BR via **Gemini API Free Tier (R$ 0,00)**.

## Garantias de Arquitetura

1. **Consumo Zero fora de leitura (sem app em segundo plano):**
   - **Nunca** inicia com o Windows.
   - O motor local (`backend/server.py`) só é ligado ao clicar em **Ligar Motor** na extensão (via Chrome Native Messaging Host em `HKCU`) ou ao executar `start.bat`.
   - Ao clicar em **Desligar Motor** (`POST /shutdown`) ou após **5 minutos de inatividade** (configurável), descarrega 100% dos modelos da RTX 5060 Ti e encerra o processo → **0% CPU, 0 MB RAM, 0 MB VRAM**.
   - Enquanto ativo em leitura, mantém detector + OCR quentes na VRAM (abaixo do orçamento seguro de 3–4 GB) para respostas rápidas entre páginas do capítulo.
2. **Custo Zero (R$ 0,00):**
   - Whitelist explícita de modelos gratuitos (`FREE_TIER_MODELS` em `poc/config.py`, padrão `gemini-3.5-flash-lite`), sem modelos pagos e sem fallback pago.
   - 1 única chamada JSON agrupada por página contendo **apenas os textos do OCR**; páginas com 0 balões nunca chamam o Gemini (cache negativo).
3. **Resiliência + Cache Local:**
   - Circuit Breaker (`CLOSED` / `OPEN` / `HALF_OPEN`, backoff exponencial + jitter, `Retry-After`) com pausa automática de prefetch, preservação das páginas já traduzidas e botão **Testar Retomada** na UI.
   - Cache SHA-256 no backend (`poc/cache/`) para retries instantâneos sem refazer GPU + cache em **IndexedDB** na extensão.

## Guia Rápido em 3 Passos (Chrome + Native Host)

1. **Carregar a extensão no Chrome:**
   - Abra `chrome://extensions/`, ative o **Modo do desenvolvedor** (canto superior direito), clique em **Carregar sem compactação** e selecione a pasta:
     `C:\Users\lucas\Desktop\Scanlator Vogels-pt-BR\extension\dist`
   - Copie o **ID de 32 letras** exibido no cartão da extensão.
2. **Registrar o Native Messaging Host (apenas `HKCU`, sem administrador):**
   - Execute:
     ```powershell
     & "native_host\install_native_host.bat" SEU_ID_DE_32_LETRAS
     ```
   - *(Opcional: se preferir iniciar manualmente antes de registrar, basta rodar `start.bat`.)*
3. **Ler e traduzir:**
   - Abra qualquer leitor de mangá, clique no ícone da extensão (ou no botão discreto `SV` no canto inferior direito), clique em **Ligar Motor** e use **Traduzir página atual** ou ative **Auto Translate**.

## Comandos Úteis
- **Rodar todos os testes (Circuit Breaker + FastAPI):**
  ```powershell
  .venv\Scripts\python.exe -m pytest -v poc/tests backend/tests
  ```
- **Recompilar a extensão MV3 (`extension/dist`):**
  ```powershell
  npm.cmd --prefix extension run build
  ```
- **Rodar a PoC CLI em `poc/input/test.jpg`:**
  ```powershell
  .venv\Scripts\python.exe -m poc.run_poc
  ```
- **Rodar o benchmark real de servidor quente + shutdown:**
  ```powershell
  .venv\Scripts\python.exe -m backend.benchmark_warm_server
  ```
