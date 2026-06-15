import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from services.runtime.git import Git, GitError
from utils.paths import _project_root

console = Console()


def update(
    tag: Optional[str] = typer.Option(None, "--tag", "--ref", help="Pin CoStaff core to a release tag, branch, or commit (e.g. v0.1.0-alpha-1). Without this flag the command fast-forwards to whatever main currently points at."),
    all_plugins: bool = typer.Option(False, "--all", help="After updating the core, re-pin and rebuild every registered agent and channel to the same --tag (or pull latest when no --tag). Requires Docker — this rebuilds containers."),
):
    """Pull the latest CoStaff updates from GitHub."""
    if tag:
        console.print(Panel.fit(f"🔄 [bold blue]CoStaff Update[/bold blue] → [magenta]{tag}[/magenta]"))
    else:
        console.print(Panel.fit("🔄 [bold blue]CoStaff Update[/bold blue]"))
    console.print(f"Pulling latest changes in [bold]{_project_root}[/bold]...")

    # Check for local modifications
    status = subprocess.run(["git", "status", "--porcelain"], cwd=_project_root, capture_output=True, text=True)
    if status.stdout.strip():
        console.print("[yellow]Warning: You have local modifications. Updates may fail.[/yellow]")
        console.print("[dim]Hint: Run 'git checkout .' to discard local changes if you get a conflict.[/dim]")

    if tag:
        # Tag-pinned path: fetch refs + tags, then check out. We do NOT
        # pull/merge — that would refuse on detached HEAD anyway. After
        # checkout the working tree is detached at the tag/commit (or
        # attached if `tag` happens to be a branch name); either is fine
        # for a runtime install.
        git = Git()
        try:
            git.fetch_tags(_project_root)
            git.checkout(_project_root, tag)
        except GitError as e:
            console.print(f"[red]{e}[/red]")
            console.print("\n[bold red]Update failed.[/bold red]")
            raise SystemExit(1)
        console.print(f"[bold green]Checked out {tag}.[/bold green] Run 'costaff restart' to apply any changes.")
    else:
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=_project_root,
            capture_output=True,
            text=True,
        )

        if result.stdout:
            console.print(result.stdout.rstrip())

        if result.returncode != 0:
            if result.stderr:
                console.print(f"[red]{result.stderr.rstrip()}[/red]")
            console.print("\n[bold red]Update failed.[/bold red]")
            if "not a git repository" in result.stderr:
                console.print("[yellow]Error: CoStaff is not installed as a git repository. Manual update required.[/yellow]")
            elif "local changes to the following files" in result.stderr:
                console.print("[yellow]Error: Conflicting local changes detected.[/yellow]")
                console.print("To fix, run: [bold cyan]git checkout .[/bold cyan] and then try [bold cyan]costaff update[/bold cyan] again.")
            raise SystemExit(1)

        console.print("[bold green]Up to date! Run 'costaff restart' to apply any changes.[/bold green]")

    # Re-install CLI in-place so new dependencies take effect
    pip = str(Path(sys.executable).parent / "pip")
    console.print("Re-installing CLI dependencies...")
    subprocess.run([pip, "install", "-e", _project_root, "-q"], check=False)
    console.print("[green]Done.[/green]")

    if all_plugins:
        _update_all_plugins(tag)


def _update_all_plugins(tag: Optional[str]) -> None:
    """Re-pin + rebuild every source-based agent and channel to `tag`.

    Reuses the per-plugin `agent rebuild` / `channel rebuild` commands so the
    git-checkout, config-pin, and container-rebuild semantics stay identical
    to running them by hand. Each plugin is isolated: a failure on one is
    reported and the loop moves on instead of aborting the whole batch.
    Remote (`type: "url"`) agents have no source tree to check out and are
    skipped.
    """
    # Lazy imports avoid any import cycle at CLI load time.
    from services.config import ConfigManager
    from cli.commands.agent_container import agent_rebuild
    from cli.commands.channel import channel_rebuild

    conf = ConfigManager.get_config()
    agents = [
        n for n, e in conf.get("external_agents", {}).items()
        if e.get("type") == "github" and e.get("fragment_path")
    ]
    channels = [
        n for n, e in conf.get("dynamic_channels", {}).items()
        if e.get("fragment_path")
    ]
    skipped = [
        n for n, e in conf.get("external_agents", {}).items()
        if e.get("type") != "github" or not e.get("fragment_path")
    ]

    total = len(agents) + len(channels)
    if total == 0:
        console.print("[dim]No source-based agents or channels to rebuild.[/dim]")
        return

    target = f"→ [magenta]{tag}[/magenta]" if tag else "→ latest on each plugin's branch"
    console.print(Panel.fit(f"📦 [bold blue]Updating {total} plugin(s)[/bold blue] {target}"))

    ok: list[str] = []
    failed: list[str] = []
    for kind, names, rebuild in (
        ("Agent", agents, agent_rebuild),
        ("Channel", channels, channel_rebuild),
    ):
        for n in names:
            console.print(f"\n[bold]{kind}[/bold] [cyan]{n}[/cyan]")
            try:
                rebuild(name=n, no_cache=False, pull=True, tag=tag)
                ok.append(n)
            except (typer.Exit, SystemExit, Exception) as e:  # noqa: BLE001
                # Per-plugin isolation: log and keep going. The rebuild
                # command already printed the specific error before exiting.
                if not isinstance(e, (typer.Exit, SystemExit)):
                    console.print(f"[red]{n}: {e}[/red]")
                failed.append(n)

    summary = f"\n[bold green]Rebuilt {len(ok)}/{total} plugin(s).[/bold green]"
    if failed:
        summary += f" [red]{len(failed)} failed: {', '.join(failed)}[/red]"
    console.print(summary)
    if skipped:
        console.print(f"[dim]Skipped {len(skipped)} remote agent(s) (no source to pin): {', '.join(skipped)}[/dim]")
