import os
import sys
import json
import time
import questionary
from dotenv import set_key

# Re-exports — existing callers do `from utils.helpers import VERSION, PATHS, ...`
# and shouldn't have to change. Each domain now lives in its own module:
#   utils.paths          paths/constants
#   utils.serialization  datetime / row serialization
#   utils.validators     cron + a2a URL safety
#   utils.ports          dynamic port allocation
from .paths import (
    VERSION, PATHS,
    _project_root, _base_dir, _runtime_root, _workspace_root,
)
from .serialization import _dt_to_z, _serialize_row
from .validators import _validate_cron, _validate_a2a_url
from .ports import _next_available_port, _next_available_channel_port
from .plugin_env import (
    DEFAULT_GEMINI_MODEL,
    _prompt_model_config,
    _prompt_and_write_plugin_env,
)


def _write_channel_fragment(name: str, source_path: str, public_port: int, plugin_env_path: str) -> tuple[str, list, dict]:
    """Generate compose-fragment.yaml from the source docker-compose.yaml. Returns (fragment_path, ext_services, manifest)."""
    import yaml as _yaml

    manifest_path = os.path.join(source_path, "costaff.channel.json")
    if not os.path.exists(manifest_path):
        manifest_path = os.path.join(source_path, "costaff.agent.json")
    compose_path = os.path.join(source_path, "docker-compose.yaml")

    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"Manifest not found in {source_path}")
    if not os.path.exists(compose_path):
        raise FileNotFoundError(f"docker-compose.yaml not found in {source_path}")

    with open(manifest_path) as f:
        manifest = json.load(f)
    a2a_service = manifest.get("a2a_service", name)
    port = manifest.get("port", 80)

    with open(compose_path) as f:
        src_compose = _yaml.safe_load(f)

    # Plan B: channels get shared bind mount only (read agent results, accept uploads)
    shared_host_dir = os.path.join(_workspace_root, "shared")
    os.makedirs(shared_host_dir, exist_ok=True)
    CONTAINER_SHARED = "/app/data/shared"

    services_fragment = {}
    for svc, svc_def in src_compose.get("services", {}).items():
        ext_svc = f"costaff-channel-{name}-{svc}" if svc != a2a_service else f"costaff-channel-{name}"
        svc_def = svc_def.copy()
        # Force container_name so downstream tooling can find containers by names in config.json
        svc_def["container_name"] = ext_svc
        if "build" in svc_def:
            build = svc_def["build"]
            if isinstance(build, str):
                svc_def["build"] = os.path.join(source_path, build)
            elif isinstance(build, dict) and "context" in build:
                svc_def["build"]["context"] = os.path.join(source_path, build["context"])

        svc_def.pop("ports", None)
        svc_def.setdefault("networks", [])
        if "costaff_default" not in svc_def["networks"]:
            svc_def["networks"].append("costaff_default")

        if svc == a2a_service:
            svc_def.setdefault("environment", [])
            svc_def["environment"] += [f"PORT={port}"]
            svc_def["ports"] = [f"0.0.0.0:{public_port}:{port}"]

        # Inject SHARED_DIR env var
        env_list = svc_def.get("environment", [])
        if not any("SHARED_DIR=" in e for e in env_list if isinstance(e, str)):
            env_list.append(f"SHARED_DIR={CONTAINER_SHARED}")
        svc_def["environment"] = env_list

        # Replace all /app/data mounts with shared bind mount
        new_vols = []
        has_shared = False
        for vol in svc_def.get("volumes", []):
            if ":" in str(vol):
                local_part, container_part = vol.split(":", 1)
                if not local_part.startswith("/") and not local_part.startswith("./"):
                    if container_part.startswith("/app/data"):
                        if not has_shared:
                            new_vols.append(f"{shared_host_dir}:{CONTAINER_SHARED}")
                            has_shared = True
                        continue
            new_vols.append(vol)
        if not has_shared:
            new_vols.append(f"{shared_host_dir}:{CONTAINER_SHARED}")
        svc_def["volumes"] = new_vols
        svc_def["env_file"] = [PATHS["env"], plugin_env_path]
        services_fragment[ext_svc] = svc_def

    fragment = {
        "services": services_fragment,
        "networks": {"costaff_default": {"external": True}},
    }
    fragment_dir = os.path.dirname(plugin_env_path)
    fragment_path = os.path.join(fragment_dir, "compose-fragment.yaml")
    if os.path.exists(fragment_path):
        os.remove(fragment_path)

    with open(fragment_path, "w") as f:
        _yaml.dump(fragment, f, default_flow_style=False, allow_unicode=True)

    return fragment_path, list(services_fragment.keys()), manifest


def _deploy_local_channel(name: str, source_path: str, conf: dict, predefined_envs: dict = None, build_only: bool = False) -> dict:
    """Build (and optionally start) a local-path communication channel following CoStaff Convention."""
    from dotenv import load_dotenv
    from services.docker import DockerManager

    source_path = os.path.abspath(source_path)
    manifest_path = os.path.join(source_path, "costaff.channel.json")
    if not os.path.exists(manifest_path):
        manifest_path = os.path.join(source_path, "costaff.agent.json")
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"Manifest not found in {source_path}")

    with open(manifest_path) as f:
        manifest = json.load(f)
    description = manifest.get("description", "")

    public_port = _next_available_channel_port(conf)
    fragment_dir = os.path.join(_base_dir, "costaff-channel", name)
    os.makedirs(fragment_dir, exist_ok=True)

    plugin_env_path = _prompt_and_write_plugin_env(manifest, fragment_dir, predefined_envs)
    load_dotenv(PATHS["env"], override=True)

    fragment_path, ext_services, _ = _write_channel_fragment(name, source_path, public_port, plugin_env_path)

    from rich.console import Console
    console = Console()
    main_compose = os.path.join(_runtime_root, "docker-compose.yaml")
    import subprocess
    if build_only:
        cmd = DockerManager.get_cmd() + ["-f", main_compose, "-f", fragment_path, "build"] + ext_services
        console.print(f"Building channel {name}...")
    else:
        cmd = DockerManager.get_cmd() + ["-f", main_compose, "-f", fragment_path, "up", "-d", "--build", "--force-recreate"]
        console.print(f"Building and starting channel {name}...")
    subprocess.run(cmd, cwd=_project_root)

    return {
        "type": "github",
        "source_path": source_path,
        "fragment_path": fragment_path,
        "public_port": public_port,
        "description": description,
        "enabled": True,
        "container_names": ext_services,
    }


def _deploy_local_agent(name: str, source_path: str, conf: dict, predefined_envs: dict = None) -> dict:
    """Build and start a local-path agent following CoStaff Agent Convention."""
    import yaml as _yaml
    from dotenv import load_dotenv
    from services.docker import DockerManager

    source_path = os.path.abspath(source_path)
    manifest_path = os.path.join(source_path, "costaff.agent.json")
    compose_path = os.path.join(source_path, "docker-compose.yaml")

    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"costaff.agent.json not found in {source_path}")
    if not os.path.exists(compose_path):
        raise FileNotFoundError(f"docker-compose.yaml not found in {source_path}")

    with open(manifest_path) as f:
        manifest = json.load(f)

    a2a_service = manifest.get("a2a_service", name)
    port = manifest.get("port", 8081)
    description = manifest.get("description", "")
    version = manifest.get("version", "")

    public_port = _next_available_port(conf)
    fragment_dir = os.path.join(_base_dir, "costaff-agent", name)
    os.makedirs(fragment_dir, exist_ok=True)

    plugin_env_path = _prompt_and_write_plugin_env(manifest, fragment_dir, predefined_envs)
    load_dotenv(PATHS["env"], override=True)

    # Plan B workspace directories: private per-agent + shared
    agent_container_name = f"costaff-agent-{name}"
    private_host_dir = os.path.join(_workspace_root, agent_container_name)
    shared_host_dir = os.path.join(_workspace_root, "shared")
    agent_shared_host_dir = os.path.join(shared_host_dir, agent_container_name)
    os.makedirs(private_host_dir, exist_ok=True)
    os.makedirs(agent_shared_host_dir, exist_ok=True)

    # Env vars inside container (Plan B naming convention)
    NAME_UPPER = name.upper().replace("-", "_")
    AGENT_WORKSPACE_ENV_KEY = f"AGENT_WORKSPACE_DIR_{NAME_UPPER}"
    COSTAFF_SHARED_ENV_KEY = f"COSTAFF_SHARED_DIR_{NAME_UPPER}"
    CONTAINER_WORKSPACE = "/app/data"
    CONTAINER_SHARED = "/app/data/shared"
    CONTAINER_MY_SHARED = f"/app/data/shared/{agent_container_name}"

    # Read source compose
    with open(compose_path) as f:
        src_compose = _yaml.safe_load(f)
    service_names = list(src_compose.get("services", {}).keys())

    def _svc_to_container(svc, svc_def):
        explicit = svc_def.get("container_name")
        if explicit:
            return explicit
        return f"costaff-{svc}" if svc.startswith("costaff-") else f"costaff-{svc}"

    a2a_container_name_val = _svc_to_container(a2a_service, src_compose["services"].get(a2a_service, {}))

    services_fragment = {}
    for svc in service_names:
        src_def = src_compose["services"][svc]
        ext_svc = _svc_to_container(svc, src_def)
        svc_def = src_def.copy()
        # Rewrite build context to absolute path
        if "build" in svc_def:
            build = svc_def["build"]
            if isinstance(build, str):
                svc_def["build"] = os.path.join(source_path, build)
            elif isinstance(build, dict) and "context" in build:
                svc_def["build"]["context"] = os.path.join(source_path, build["context"])
        # Remove original ports (we manage them)
        svc_def.pop("ports", None)
        # Add to costaff network
        svc_def.setdefault("networks", [])
        if isinstance(svc_def["networks"], list) and "costaff_default" not in svc_def["networks"]:
            svc_def["networks"].append("costaff_default")
        # Inject fixed runtime vars into a2a service
        if svc == a2a_service:
            svc_def.setdefault("environment", [])
            svc_def["environment"] += [f"PORT={port}", f"PUBLIC_HOST={ext_svc}"]
            svc_def["ports"] = [f"127.0.0.1:{public_port}:{port}"]
        # Rename depends_on references
        if "depends_on" in svc_def:
            old_deps = svc_def["depends_on"]
            if isinstance(old_deps, list):
                svc_def["depends_on"] = [
                    _svc_to_container(d, src_compose["services"].get(d, {}))
                    for d in old_deps
                ]
        svc_def["container_name"] = ext_svc
        services_fragment[ext_svc] = svc_def

    # Inject Plan B env vars and volumes into all services
    skip_env_keys = {"WORKSPACE_DIR", "DATA_DIR", "SHARED_DIR", AGENT_WORKSPACE_ENV_KEY, COSTAFF_SHARED_ENV_KEY}
    for svc_name, svc_def in services_fragment.items():
        svc_def.setdefault("environment", [])
        # Strip old workspace-related vars that we'll replace
        updated_envs = [
            e for e in svc_def["environment"]
            if not ("=" in e and (
                e.split("=", 1)[0] in skip_env_keys
                or e.split("=", 1)[0].endswith("_WORKSPACE_DIR")
                or e.split("=", 1)[0].startswith("COSTAFF_SHARED_DIR_")
            ))
        ]
        updated_envs += [
            f"WORKSPACE_DIR={CONTAINER_WORKSPACE}",
            f"SHARED_DIR={CONTAINER_SHARED}",
            f"{COSTAFF_SHARED_ENV_KEY}={CONTAINER_MY_SHARED}",
            f"{AGENT_WORKSPACE_ENV_KEY}={CONTAINER_WORKSPACE}",
        ]
        svc_def["environment"] = updated_envs

        # Plan B volumes: private bind mount + shared bind mount
        new_vols = [
            f"{private_host_dir}:{CONTAINER_WORKSPACE}",
            f"{shared_host_dir}:{CONTAINER_SHARED}",
        ]
        for vol in svc_def.get("volumes", []):
            if ":" in str(vol):
                _, container_part = str(vol).split(":", 1)
                if container_part.startswith("/app/data"):
                    continue
            new_vols.append(vol)
        svc_def["volumes"] = new_vols

    # Inject env_file into all services
    for svc_def in services_fragment.values():
        svc_def["env_file"] = [PATHS["env"], plugin_env_path]

    fragment = {
        "services": services_fragment,
        "networks": {"costaff_default": {"external": True}},
    }
    fragment_path = os.path.join(fragment_dir, "compose-fragment.yaml")
    if os.path.exists(fragment_path):
        os.remove(fragment_path)

    with open(fragment_path, "w") as f:
        _yaml.dump(fragment, f, default_flow_style=False, allow_unicode=True)

    # Build & start
    import httpx
    from rich.console import Console
    console = Console()
    main_compose = os.path.join(_runtime_root, "docker-compose.yaml")
    ext_services = list(services_fragment.keys())
    cmd = DockerManager.get_cmd() + ["-f", main_compose, "-f", fragment_path, "up", "-d", "--build", "--force-recreate"]
    console.print(f"Building and starting {name}...")
    import subprocess
    result = subprocess.run(cmd, cwd=_project_root)
    if result.returncode != 0:
        raise RuntimeError("docker compose up failed")

    # Health check (30s)
    health_url = f"http://localhost:{public_port}/.well-known/agent-card.json"
    console.print(f"Waiting for health check at {health_url}...")
    for _ in range(10):
        time.sleep(3)
        try:
            r = httpx.get(health_url, timeout=3.0)
            if r.status_code == 200:
                console.print("[green]Agent is healthy![/green]")
                break
        except Exception:
            pass
    else:
        console.print("[yellow]Warning: health check timed out. Agent may still be starting.[/yellow]")

    result_dict = {
        "type": "github",
        "source_path": source_path,
        "fragment_path": fragment_path,
        "a2a_url": f"http://{a2a_container_name_val}:{port}",
        "public_port": public_port,
        "description": description,
        "version": version,
        "enabled": True,
        "container_names": ext_services,
    }
    if manifest.get("mcp_configurable"):
        result_dict["mcp_configurable"] = True
        result_dict["mcp_env_var"] = manifest.get("mcp_env_var", name.replace("-", "_").upper() + "_MCP_URLS")
    return result_dict
