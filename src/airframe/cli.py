"""Airframe Command-Line Interface (CLI)."""

from __future__ import annotations

import typer
from rich.console import Console

import airframe

app = typer.Typer(
    name="airframe",
    help="Airframe: The engineering chassis and operating system for AI agents and swappable models.",
    add_completion=False,
)
console = Console()


def version_callback(value: bool) -> None:
    if value:
        console.print(f"[bold cyan]Airframe[/bold cyan] version [green]{airframe.__version__}[/green]")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None,
        "--version",
        "-v",
        help="Show Airframe version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """Airframe Agent Operating System & Harness Engineering CLI."""
    pass


@app.command()
def info() -> None:
    """Display system status and registered engine adapters."""
    console.print("[bold cyan]✈️  Airframe Agent Operating System[/bold cyan]")
    console.print(f"Version: [green]{airframe.__version__}[/green]")
    console.print("\n[bold]Supported Providers:[/bold]")
    console.print("  • [cyan]gemini[/cyan] (Google Gemini 2.0 / 1.5)")
    console.print("  • [cyan]anthropic[/cyan] (Claude 3.5 Sonnet / Opus)")
    console.print("  • [cyan]openai_compatible[/cyan] (Ollama, vLLM, LM Studio, OpenAI)")


if __name__ == "__main__":
    app()
