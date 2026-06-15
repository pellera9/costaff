"""`costaff backup` / `costaff restore` — whole-install snapshot & recovery."""
import os

import questionary
import typer
from rich.console import Console
from rich.panel import Panel

from services.backup import BackupError, create_backup, read_manifest, restore_backup

console = Console()


def backup(
    output: str = typer.Argument(None, help="Output archive path (default: costaff_backup_<timestamp>.tar.gz)"),
    no_db: bool = typer.Option(False, "--no-db", help="Skip the Postgres dump"),
    no_workspace: bool = typer.Option(False, "--no-workspace", help="Skip the shared workspace data dir"),
):
    """Snapshot the whole install (.env, config, database, workspace) to one archive."""
    console.print(Panel.fit("📦 [bold blue]CoStaff Backup[/bold blue]"))
    try:
        path = create_backup(
            output,
            include_db=not no_db,
            include_workspace=not no_workspace,
        )
    except BackupError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    size_mb = os.path.getsize(path) / (1024 * 1024)
    console.print(f"[green]✔ Backup written to[/green] [bold]{path}[/bold] [dim]({size_mb:.1f} MB)[/dim]")
    console.print("[dim]Keep it somewhere safe — it contains your secrets and database.[/dim]")


def restore(
    archive: str = typer.Argument(..., help="Backup archive (.tar.gz) created by 'costaff backup'"),
    no_db: bool = typer.Option(False, "--no-db", help="Do not restore the database"),
    no_workspace: bool = typer.Option(False, "--no-workspace", help="Do not restore the workspace dir"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the overwrite confirmation"),
):
    """Restore a full install from a 'costaff backup' archive (destructive)."""
    console.print(Panel.fit("♻️  [bold blue]CoStaff Restore[/bold blue]"))
    if not os.path.exists(archive):
        console.print(f"[red]Archive not found: {archive}[/red]")
        raise typer.Exit(1)

    try:
        manifest = read_manifest(archive)
    except BackupError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    console.print(
        f"Archive made by CoStaff [bold]{manifest.get('costaff_version', '?')}[/bold] "
        f"at [bold]{manifest.get('created_at', '?')}[/bold]; "
        f"contents: [cyan]{', '.join(manifest.get('contents', [])) or '(none)'}[/cyan]"
    )
    console.print(
        "[yellow]This overwrites your current .env, config.json, workspace, and database.[/yellow]"
    )
    if not yes and not questionary.confirm("Proceed with restore?").ask():
        console.print("[dim]Aborted.[/dim]")
        raise typer.Exit(0)

    try:
        restore_backup(
            archive,
            include_db=not no_db,
            include_workspace=not no_workspace,
        )
    except BackupError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    console.print("[green]✔ Restore complete.[/green]")
    console.print("[bold]Next:[/bold] run [cyan]costaff restart[/cyan] so services pick up the restored config and data.")
