"""CLI runner for Scanlator Vogels PT-BR — Stage 1 PoC (on-demand, zero background usage).

Executes the complete pipeline on `poc/input/test.jpg` (or `--input`), exports images &
`benchmark.json`, prints a detailed performance/VRAM table, frees 100% VRAM/RAM, and exits.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table

from poc.config import (
    FREE_TIER_MODELS,
    INPUT_DIR,
    OUTPUT_DIR,
    PoCConfig,
    PaidOrDisallowedModelError,
    validate_free_tier_model,
)
from poc.pipeline import ScanlationPipeline
from poc.schemas import BenchmarkReport
from poc.translator import GeminiFreeTierTranslator, MissingAPIKeyError
from poc.vram import release_gpu_memory

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
console = Console()


def _find_default_input() -> Path:
    preferred = INPUT_DIR / "test.jpg"
    if preferred.exists():
        return preferred
    if INPUT_DIR.exists():
        for p in sorted(INPUT_DIR.iterdir()):
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
                return p
    raise FileNotFoundError(
        f"Nenhuma imagem encontrada em {INPUT_DIR}. Coloque uma pagina em poc/input/."
    )


def _print_benchmark(report: BenchmarkReport) -> None:
    console.print()
    console.rule("[bold cyan]SCANLATOR VOGELS PT-BR — BENCHMARK ETAPA 1 (PoC)[/bold cyan]")
    console.print(
        f"[bold]Imagem:[/bold] {report.image_path} ({report.image_resolution[0]}x{report.image_resolution[1]})  |  "
        f"[bold]GPU:[/bold] {report.gpu_name} (PyTorch {report.torch_version}, CUDA {report.cuda_version})"
    )
    console.print(
        f"[bold]Blocos:[/bold] {report.total_blocks_detected} detectados / {report.blocks_with_text} com texto  |  "
        f"[bold]Inpainting:[/bold] {report.opencv_inpainted_blocks} OpenCV solido + {report.lama_inpainted_blocks} LaMa recorte  |  "
        f"[bold]Gemini:[/bold] {report.gemini_model} ({'OK' if report.translation_executed else 'pulado/local-only'})"
    )

    table = Table(show_header=True, header_style="bold magenta", expand=True)
    table.add_column("Etapa", style="bold")
    table.add_column("Device")
    table.add_column("Cache")
    table.add_column("Tempo (s)", justify="right")
    table.add_column("Pico RAM (MB)", justify="right")
    table.add_column("Torch VRAM Pico (MB)", justify="right")
    table.add_column("NVIDIA VRAM (Antes/Pico/Pos)", justify="right")

    for st in report.stages:
        nv_str = "N/A"
        if st.nvidia_used_before_mb is not None:
            nv_str = (
                f"{st.nvidia_used_before_mb:.0f} / "
                f"{(st.nvidia_used_peak_mb or 0):.0f} / "
                f"{(st.nvidia_used_after_unload_mb or 0):.0f}"
            )
        table.add_row(
            st.stage,
            st.device_used,
            "HIT" if st.cache_hit else "MISS",
            f"{st.duration_sec:.3f}",
            f"{st.ram_peak_mb:.1f}",
            f"{st.vram_allocated_peak_mb:.1f}",
            nv_str,
        )

    nv_total = "N/A"
    if report.nvidia_vram_start_mb is not None:
        nv_total = (
            f"{report.nvidia_vram_start_mb:.0f} / "
            f"{(report.nvidia_vram_peak_mb or 0):.0f} / "
            f"{(report.nvidia_vram_end_mb or 0):.0f}"
        )
    table.add_section()
    table.add_row(
        "[bold green]TOTAL[/bold green]",
        "-",
        "-",
        f"[bold green]{report.total_duration_sec:.3f}[/bold green]",
        f"[bold]{report.overall_ram_peak_mb:.1f}[/bold]",
        f"[bold]{report.overall_torch_vram_peak_mb:.1f}[/bold]",
        nv_total,
    )
    console.print(table)

    # Blocks dialogue table
    blk_table = Table(
        title="Baloes em Ordem de Leitura Japonesa (Direita -> Esquerda, Cima -> Baixo)",
        show_header=True,
        header_style="bold cyan",
        expand=True,
    )
    blk_table.add_column("#", justify="right", width=4)
    blk_table.add_column("Inpaint", width=13)
    blk_table.add_column("OCR (JA)")
    blk_table.add_column("Traducao (PT-BR)")

    for b in report.blocks:
        blk_table.add_row(
            str(b.id),
            b.inpaint_method or "-",
            b.source_text or "[dim](vazio)[/dim]",
            b.translated_text or "[dim](pendente — modo local)[/dim]",
        )
    console.print(blk_table)

    if report.quota is not None:
        console.print(
            f"[bold yellow]Cota Gemini Free Tier ({report.quota.date_utc}, {report.quota.model}):[/bold yellow] "
            f"reqs hoje={report.quota.requests_today} | "
            f"ultima chamada: in={report.quota.last_request_prompt_tokens}, out={report.quota.last_request_output_tokens} tokens | "
            f"total hoje={report.quota.total_tokens_today} tokens"
        )

    vram_status = "[bold green]OK (VRAM liberada)[/bold green]" if report.vram_released_cleanly else "[bold red]ATENCAO[/bold red]"
    console.print(f"[bold]Limpeza de VRAM ao encerrar:[/bold] {vram_status}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scanlator Vogels PT-BR — Etapa 1 (PoC Local + Gemini Free Tier)"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Caminho da imagem de manga (padrao: poc/input/test.jpg)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="Pasta de saida (padrao: poc/output)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help=f"Modelo Gemini Free Tier ({', '.join(sorted(FREE_TIER_MODELS))})",
    )
    parser.add_argument(
        "--device",
        choices=["auto", "cuda", "cpu"],
        default=None,
        help="Dispositivo para modelos locais (padrao: .env ou auto)",
    )
    parser.add_argument(
        "--glossary",
        type=Path,
        default=None,
        help="Arquivo JSON opcional de glossario (ex: {\"ルミネ\": \"Lumine\"})",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignora o cache local por hash SHA-256 (ideal para benchmark frio completo)",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Valida somente etapas locais (deteccao, OCR, mascaras/inpainting) sem chamar o Gemini",
    )
    parser.add_argument(
        "--no-uppercase",
        action="store_true",
        help="Desativa a renderizacao em CAIXA ALTA (padrao scanlation e CAIXA ALTA preservando acentos PT-BR)",
    )
    parser.add_argument(
        "--check-models",
        action="store_true",
        help="Consulta a API Gemini e lista modelos gratuitos da whitelist disponiveis na sua chave",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Exibe logs detalhados",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="[%(levelname)s] %(name)s: %(message)s",
    )

    try:
        config = PoCConfig.from_env()
        if args.model:
            validated_model = validate_free_tier_model(args.model)
            config = PoCConfig(
                gemini_api_key=config.gemini_api_key,
                gemini_model=validated_model,
                gemini_timeout_seconds=config.gemini_timeout_seconds,
                device_preference=args.device or config.device_preference,
                min_free_vram_mb=config.min_free_vram_mb,
                inpaint_crop_margin=config.inpaint_crop_margin,
                inpaint_max_crop_side=config.inpaint_max_crop_side,
                service=config.service,
            )
        elif args.device:
            config = PoCConfig(
                gemini_api_key=config.gemini_api_key,
                gemini_model=config.gemini_model,
                gemini_timeout_seconds=config.gemini_timeout_seconds,
                device_preference=args.device,
                min_free_vram_mb=config.min_free_vram_mb,
                inpaint_crop_margin=config.inpaint_crop_margin,
                inpaint_max_crop_side=config.inpaint_max_crop_side,
                service=config.service,
            )
    except PaidOrDisallowedModelError as exc:
        console.print(f"[bold red]ERRO DE POLITICA DE CUSTO ZERO:[/bold red] {exc}")
        return 2

    if args.check_models:
        try:
            tr = GeminiFreeTierTranslator(
                api_key=config.gemini_api_key,
                model=config.gemini_model,
            )
            models = tr.list_available_free_models()
            console.print("[bold green]Modelos gratuitos da whitelist disponiveis na sua conta:[/bold green]")
            for m in models:
                console.print(f"  - {m}")
            return 0
        except Exception as exc:
            console.print(f"[bold red]Falha ao listar modelos:[/bold red] {exc}")
            return 1

    input_path = args.input or _find_default_input()
    if not input_path.exists():
        console.print(f"[bold red]Imagem nao encontrada:[/bold red] {input_path}")
        return 1

    glossary: Optional[dict[str, str]] = None
    if args.glossary:
        glossary = json.loads(args.glossary.read_text(encoding="utf-8"))

    pipeline = ScanlationPipeline(config)
    try:
        report = pipeline.process_image(
            input_path,
            output_dir=args.output_dir,
            use_cache=not args.no_cache,
            skip_translation=args.local_only,
            glossary=glossary,
            uppercase=not args.no_uppercase,
        )
        _print_benchmark(report)
        console.print(
            f"\n[bold green]Arquivos exportados em:[/bold green] {args.output_dir.resolve()}"
        )
        return 0
    except MissingAPIKeyError as exc:
        console.print(f"\n[bold yellow]API KEY AUSENTE:[/bold yellow] {exc}")
        console.print(
            "Dica: Deteccao, OCR e Inpainting ja ficaram salvos no cache local por hash SHA-256! "
            "Assim que colar sua GEMINI_API_KEY no `.env`, basta rodar novamente."
        )
        return 3
    except Exception as exc:
        console.print(f"\n[bold red]Erro durante execucao da PoC:[/bold red] {exc}")
        logging.getLogger(__name__).exception("Detalhes do erro:")
        return 1
    finally:
        pipeline.unload_all()
        release_gpu_memory()


if __name__ == "__main__":
    sys.exit(main())
