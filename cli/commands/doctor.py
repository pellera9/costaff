import subprocess
import os

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from managers.config import ConfigManager
from managers.docker import DockerManager
from utils.helpers import PATHS, _project_root

console = Console()


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def doctor():
    """Diagnose CoStaff services and report issues."""
    console.print(Panel.fit("[bold blue]CoStaff Doctor[/bold blue]", subtitle="collecting diagnostics..."))

    # ── 1. Docker containers ─────────────────────────────────────────────────
    console.print("\n[bold]1. Container Status[/bold]")
    compose_cwd = DockerManager.get_compose_cwd("docker-compose.yaml")
    r = _run(DockerManager.get_cmd() + ["-f", "docker-compose.yaml", "ps", "--format", "table"], cwd=compose_cwd)
    if r.returncode == 0:
        console.print(r.stdout or "(no containers)")
    else:
        console.print(f"[red]docker compose ps failed:[/red] {r.stderr.strip()}")

    # ── 2. identity_maps table ───────────────────────────────────────────────
    console.print("\n[bold]2. identity_maps table[/bold]")
    try:
        from sqlalchemy import create_engine, text
        from dotenv import dotenv_values
        env = dotenv_values(PATHS["env"])
        db_uri = env.get("ADK_SESSION_SERVICE_URI", "")
        if not db_uri:
            console.print("[red]ADK_SESSION_SERVICE_URI not set in .env[/red]")
        else:
            # Replace Docker-internal service name with localhost for host-side connection
            sync_uri = (
                db_uri
                .replace("postgresql+asyncpg://", "postgresql://")
                .replace("@postgres:", "@localhost:")
            )
            engine = create_engine(sync_uri, connect_args={"connect_timeout": 5})
            with engine.connect() as conn:
                # Schema
                cols_result = conn.execute(text(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_name = 'identity_maps' ORDER BY ordinal_position"
                ))
                cols = list(cols_result)
                if cols:
                    t = Table("column", "type", title="identity_maps schema")
                    for c in cols:
                        t.add_row(c[0], c[1])
                    console.print(t)
                else:
                    console.print("[yellow]Table identity_maps does not exist yet.[/yellow]")
                    return

                # Rows
                rows = conn.execute(text(
                    "SELECT session_id, real_id, is_approved, created_at "
                    "FROM identity_maps ORDER BY created_at DESC LIMIT 20"
                ))
                rows = list(rows)
                if rows:
                    t2 = Table("session_id", "real_id", "approved", "created_at", title="Recent identities")
                    for row in rows:
                        t2.add_row(str(row[0]), str(row[1]), str(row[2]), str(row[3]))
                    console.print(t2)
                else:
                    console.print("[yellow]No rows in identity_maps — sync_identity may have failed.[/yellow]")
    except Exception as e:
        console.print(f"[red]DB error:[/red] {e}")

    # ── 3. Channel container logs (last 30 lines) ────────────────────────────
    console.print("\n[bold]3. Channel Logs (last 30 lines each)[/bold]")
    conf = ConfigManager.get_config()
    fragment_entries = conf.get("dynamic_channels", {})

    if not fragment_entries:
        console.print("[yellow]No dynamic channels configured.[/yellow]")
    else:
        for name, entry in fragment_entries.items():
            fragment_path = entry.get("fragment_path")
            container_names = entry.get("container_names", [])
            if not fragment_path or not container_names:
                continue
            main_compose = str(
                __import__("pathlib").Path(compose_cwd) / "docker-compose.yaml"
            )
            for cname in container_names:
                console.print(f"\n[cyan]── {cname} ──[/cyan]")
                r = _run(
                    DockerManager.get_cmd() + [
                        "-f", main_compose, "-f", fragment_path,
                        "logs", "--tail", "30", cname,
                    ],
                    cwd=compose_cwd,
                )
                output = (r.stdout + r.stderr).strip()
                if output:
                    # Highlight DB error lines
                    for line in output.splitlines():
                        if any(kw in line.lower() for kw in ["error", "fail", "exception", "traceback"]):
                            console.print(f"[red]{line}[/red]")
                        elif any(kw in line.lower() for kw in ["warn", "warning"]):
                            console.print(f"[yellow]{line}[/yellow]")
                        else:
                            console.print(line)
                else:
                    console.print("[dim](no output)[/dim]")

    # ── 4. Network check ─────────────────────────────────────────────────────
    console.print("\n[bold]4. Docker Network[/bold]")
    r = _run(["docker", "network", "ls", "--filter", "name=costaff_default", "--format", "{{.Name}}\t{{.Driver}}"])
    if "costaff_default" in r.stdout:
        console.print("[green]✔[/green]  costaff_default network exists")
    else:
        console.print("[red]✖[/red]  costaff_default network not found — run 'costaff start' first")

    # ── 5. .env summary ──────────────────────────────────────────────────────
    console.print("\n[bold]5. .env key variables[/bold]")
    try:
        from dotenv import dotenv_values
        env = dotenv_values(PATHS["env"])
        keys_to_show = [
            "ADK_SESSION_SERVICE_URI", "COSTAFF_AGENT_MODEL_PROVIDER",
            "GOOGLE_API_KEY", "MCP_SECRET_KEY", "API_HEADERS_KEY",
        ]
        t3 = Table("key", "value", title=PATHS["env"])
        for k in keys_to_show:
            v = env.get(k, "")
            masked = (v[:4] + "****") if len(v) > 4 else ("(not set)" if not v else "****")
            t3.add_row(k, masked)
        console.print(t3)
    except Exception as e:
        console.print(f"[red].env read error:[/red] {e}")

    console.print("\n[bold green]Doctor complete.[/bold green]")
