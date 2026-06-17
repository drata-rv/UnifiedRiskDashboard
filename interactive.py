"""
interactive.py — Rich terminal UI for refresh.py's --interactive / demo mode.

Drives the same fetch and build logic as the standard flow but wraps it in
a structured, colored terminal presentation suitable for live demos.
After the initial run the script stays alive and presents a menu so the
operator can refresh data or push edits without restarting.
"""

import logging
import sys
from datetime import datetime
from typing import Callable, List

from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.text import Text

console = Console()


def _print_banner(tenant_configs: List[dict]) -> None:
    if len(tenant_configs) <= 4:
        subtitle = "  ·  ".join(t["name"] for t in tenant_configs)
    else:
        subtitle = f"{len(tenant_configs)} tenants configured"

    body = Text(justify="center")
    body.append("UNIFIED RISK DASHBOARD\n", style="bold white")
    body.append(subtitle, style="dim white")

    console.print()
    console.print(Panel(Align.center(body), padding=(1, 6), border_style="bright_blue"))
    console.print()


def _do_fetch_and_build(args, tenant_configs: List[dict], fetch_fn: Callable) -> list:
    """Run one fetch + build cycle. Returns the tenant_data list."""
    console.rule("[dim]Connecting to Drata[/dim]", style="dim")
    console.print()

    tenant_data = []
    for config in tenant_configs:
        name = config["name"]
        with console.status(f"  [cyan]{name}[/cyan]  fetching risks...", spinner="dots"):
            result = fetch_fn(config)
        tenant_data.append(result)

        if result.error:
            console.print(f"  [red]✗[/red]  [cyan]{name}[/cyan]  [red]{result.error}[/red]")
        else:
            risk_count = len(result.all_risks)
            reg_count  = len(result.registers)
            console.print(
                f"  [green]✓[/green]  [cyan]{name}[/cyan]  "
                f"[white]{risk_count}[/white] risk{'s' if risk_count != 1 else ''}  ·  "
                f"[white]{reg_count}[/white] register{'s' if reg_count != 1 else ''}"
            )

    console.print()

    if args.dry_run:
        console.rule("[yellow]Dry run — no file written[/yellow]", style="yellow")
        console.print()
        return tenant_data

    from excel_builder import ExcelBuilder

    console.rule("[dim]Building workbook[/dim]", style="dim")
    console.print()

    refreshed_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    with console.status("  [dim]Generating workbook...[/dim]", spinner="dots"):
        ExcelBuilder().build(tenant_data, output_path=args.output, refreshed_at=refreshed_at)

    console.print("  [green]✓[/green]  Risk heatmaps generated")
    console.print("  [green]✓[/green]  All Risks view compiled")
    console.print("  [green]✓[/green]  Formatting applied")
    console.print()

    success     = [t for t in tenant_data if not t.error]
    failed      = [t for t in tenant_data if t.error]
    total_risks = sum(len(t.all_risks)  for t in success)
    total_regs  = sum(len(t.registers) for t in success)
    n           = len(success)

    summary = Text(justify="center")
    summary.append("✓  Dashboard ready\n\n", style="bold green")
    summary.append(f"{args.output}\n",        style="white")
    summary.append(
        f"{n} tenant{'s' if n != 1 else ''}  ·  "
        f"{total_risks} risk{'s' if total_risks != 1 else ''}  ·  "
        f"{total_regs} register{'s' if total_regs != 1 else ''}",
        style="dim white",
    )

    console.print(Panel(Align.center(summary), padding=(1, 6), border_style="green"))
    console.print()

    if failed:
        console.print("[yellow]  Some tenants encountered errors:[/yellow]")
        for t in failed:
            console.print(f"  [red]✗[/red]  {t.name}: {t.error}")
        console.print()

    return tenant_data


def _push_interactive(args) -> None:
    """Handle the push-changes flow from the interactive menu."""
    from push_changes import push_changes

    console.print()
    dry_run = Confirm.ask("  Preview changes before pushing?", default=True)
    console.print()

    try:
        push_changes(
            input_path=args.output,
            tokens_path=args.tokens,
            dry_run=dry_run,
            azure=args.azure,
        )
    except SystemExit:
        pass  # push_changes exits with 1 on errors — don't kill the interactive session

    if dry_run:
        console.print()
        if Confirm.ask("  Push these changes to Drata?", default=False):
            console.print()
            try:
                push_changes(
                    input_path=args.output,
                    tokens_path=args.tokens,
                    dry_run=False,
                    azure=args.azure,
                )
            except SystemExit:
                pass


def _show_menu() -> int:
    console.print()
    console.print("  [dim]What would you like to do next?[/dim]")
    console.print()
    console.print("  [white][[1]][/white]  Pull latest risks from Drata")
    console.print("  [white][[2]][/white]  Sync marked changes to Drata")
    console.print("  [white][[3]][/white]  Exit")
    console.print()
    return int(Prompt.ask("  →", choices=["1", "2", "3"], default="3"))


def run_interactive(args, tenant_configs: List[dict], fetch_fn: Callable) -> None:
    """
    Full interactive (demo) flow for refresh.py.
    Replaces standard logging with structured rich output and stays alive
    after the initial run so the operator can refresh or push without restarting.
    """
    logging.getLogger().setLevel(logging.CRITICAL)

    _print_banner(tenant_configs)
    _do_fetch_and_build(args, tenant_configs, fetch_fn)

    while True:
        choice = _show_menu()
        if choice == 1:
            _do_fetch_and_build(args, tenant_configs, fetch_fn)
        elif choice == 2:
            _push_interactive(args)
        else:
            console.print()
            break
