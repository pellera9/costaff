import re
import typer
import questionary
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
import subprocess
import os
import shutil
import sys
import json
import time
from pathlib import Path

# Resolve project root: prefer CWD if it looks like the project (has mateclaw.py or setup.py),
# otherwise fall back to the directory containing this file (works for editable installs).
def _find_project_root() -> str:
    cwd = Path.cwd()
    if (cwd / "setup.py").exists() or (cwd / "mateclaw.py").exists():
        return str(cwd)
    return str(Path(__file__).resolve().parent)

_project_root = _find_project_root()
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import httpx
import webbrowser
import psutil
import threading
import hashlib
import secrets
import uvicorn
import uuid
import asyncio
import ipaddress
import socket
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse
from dotenv import load_dotenv, set_key
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from fastapi import FastAPI, HTTPException, Depends, Header, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from cryptography.fernet import Fernet, InvalidToken

# --- Constants ---
VERSION = "0.2.4"
PATHS = {
    "env": os.path.join(_project_root, ".mateclaw", ".env"),
    "config": os.path.join(_project_root, ".mateclaw", "config.json"),
    "auth": os.path.join(_project_root, ".mateclaw", "auth.json"),
    "frontend": os.path.join(_project_root, "frontend"),
    "db_local": "sqlite:///" + os.path.join(_project_root, ".mateclaw", "data", "mate_agent.db")
}

console = Console()

# --- Models ---
class LoginRequest(BaseModel):
    username: str
    password: str

class SetupRequest(BaseModel):
    username: str
    password: str

class ServiceActionRequest(BaseModel):
    action: str  # start, stop, restart

class AddMCPRequest(BaseModel):
    name: str
    config: Optional[Dict] = None
    is_external: bool = False
    url: Optional[str] = None

class AddReminderRequest(BaseModel):
    run_at: Optional[str] = None
    cron: Optional[str] = None
    channel: str
    recipient: str
    prompt: str

class TaskCreateRequest(BaseModel):
    title: str
    spec: str
    cron: Optional[str] = None
    channel: Optional[str] = None
    recipient: Optional[str] = None

class TaskUpdateRequest(BaseModel):
    title: Optional[str] = None
    spec: Optional[str] = None
    status: Optional[str] = None
    cron: Optional[str] = None
    channel: Optional[str] = None
    recipient: Optional[str] = None
    result: Optional[str] = None

class GatewayUpdateRequest(BaseModel):
    platform: str
    config: Dict

class ApiConfigCreateRequest(BaseModel):
    name: str
    url: str
    method: str = "GET"
    headers: Optional[Dict] = None  # Plain dict; will be encrypted before storage
    description: Optional[str] = None
    user_id: Optional[str] = None
    agent_ids: Optional[str] = None  # Comma-separated agent IDs or __all__

class ApiConfigUpdateRequest(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    method: Optional[str] = None
    headers: Optional[Dict] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    agent_ids: Optional[str] = None

class SkillConfigCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    tags: Optional[str] = None
    usage: Optional[str] = None
    user_id: Optional[str] = None
    agent_ids: Optional[str] = None  # Comma-separated agent IDs or __all__

class SkillConfigUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[str] = None
    usage: Optional[str] = None
    is_active: Optional[bool] = None
    agent_ids: Optional[str] = None

class AgentMCPConfigRequest(BaseModel):
    agent_id: str
    mcps: List[str]

class ExternalAgentAddRequest(BaseModel):
    name: str
    a2a_url: str
    description: Optional[str] = None

class ExternalAgentUpdateRequest(BaseModel):
    a2a_url: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None

# --- API Config Helpers ---

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]

def is_safe_url(url: str) -> bool:
    """Return False if the URL resolves to a private/loopback IP (SSRF protection)."""
    try:
        host = urlparse(url).hostname
        if not host:
            return False
        resolved = socket.getaddrinfo(host, None)
        for item in resolved:
            ip = ipaddress.ip_address(item[4][0])
            if any(ip in net for net in _PRIVATE_NETWORKS):
                return False
        return True
    except Exception:
        return False

def _get_fernet() -> Optional[Fernet]:
    key = os.getenv("API_HEADERS_KEY")
    if not key:
        return None
    try:
        return Fernet(key.encode())
    except Exception:
        return None

def encrypt_headers(headers: Dict) -> str:
    f = _get_fernet()
    raw = json.dumps(headers).encode()
    return f.encrypt(raw).decode() if f else json.dumps(headers)

def decrypt_headers(encrypted: str) -> Dict:
    f = _get_fernet()
    if f:
        try:
            return json.loads(f.decrypt(encrypted.encode()).decode())
        except InvalidToken:
            pass
    try:
        return json.loads(encrypted)
    except Exception:
        return {}

# --- Managers ---

class ConfigManager:
    @staticmethod
    def get_config() -> Dict[str, Any]:
        if os.path.exists(PATHS["config"]):
            try:
                with open(PATHS["config"], "r") as f:
                    conf = json.load(f)
                    conf.setdefault("external_mcp", {})
                    conf.setdefault("gateways_config", {})
                    conf.setdefault("mcp", ["mateclaw"])
                    # Migrate legacy "platforms" key to "channels"
                    if "platforms" in conf and "channels" not in conf:
                        conf["channels"] = conf.pop("platforms")
                    conf.setdefault("channels", [])
                    conf.setdefault("require_approval", True)
                    conf.setdefault("coding_agent_enabled", False)
                    conf.setdefault("external_agents", {})
                    # Migrate legacy coding_agent_enabled → external_agents once
                    migrated = ConfigManager._migrate_coding_agent(conf)
                    if migrated:
                        ConfigManager.save_config(conf)
                    return conf
            except: pass
        return {"channels": [], "mcp": ["mateclaw"], "external_mcp": {}, "gateways_config": {}, "mode": "standard", "db_type": "sqlite", "require_approval": True, "coding_agent_enabled": False, "external_agents": {}}

    @staticmethod
    def _migrate_coding_agent(conf: Dict) -> bool:
        """One-time migration: coding_agent_enabled → external_agents entry. Returns True if migrated."""
        if "coding-agent" in conf.get("external_agents", {}):
            return False  # already migrated
        if not conf.get("coding_agent_enabled"):
            return False  # never enabled, nothing to migrate
        load_dotenv(PATHS["env"])
        a2a_url = os.getenv("CODING_A2A_URL", "").strip() or os.getenv("CODING_A2A_INTERNAL_URL", "http://coding-agent:8081")
        conf.setdefault("external_agents", {})["coding-agent"] = {
            "type": "github",
            "a2a_url": a2a_url,
            "description": "寫程式並執行來解決需要計算、資料處理或程式邏輯的問題，回傳執行結果與產生的檔案路徑。",
            "enabled": bool(conf.get("coding_agent_enabled", False)),
            "container_names": ["coding-agent", "mcp-coding"],
        }
        return True

    @staticmethod
    def save_config(config: Dict[str, Any]):
        os.makedirs(os.path.dirname(PATHS["config"]), exist_ok=True)
        with open(PATHS["config"], "w") as f:
            json.dump(config, f, indent=2)

    @staticmethod
    def update_mcp_urls():
        conf = ConfigManager.get_config()
        urls = {}
        mode = conf.get("mode", "standard")

        for m in conf.get("mcp", []):
            if m == "mateclaw":
                path = os.path.join("mcp_servers", "mateclaw", "server.json")
            else:
                path = None

            custom_url = None
            if path and os.path.exists(path):
                try:
                    with open(path, "r") as f:
                        pkg = json.load(f).get("packages", [{}])[0]
                        custom_url = pkg.get("transport", {}).get("url")
                except: pass

            default_port = 8081 if m == "mateclaw" else 8080
            urls[m] = custom_url or f"http://{'mateclaw-mcp-' if mode == 'local' else 'mcp-'}{m}:{default_port}/sse"

        # external_mcp supports both legacy string URLs and Dive-format objects
        for name, val in conf.get("external_mcp", {}).items():
            if isinstance(val, str):
                urls[name] = val
            elif isinstance(val, dict) and val.get("enabled", True):
                if url := val.get("url"):
                    # Pass full Dive object so agent receives headers, transport etc.
                    urls[name] = {k: v for k, v in val.items() if k not in ("enabled", "description")}

        os.makedirs(os.path.dirname(PATHS["env"]), exist_ok=True)
        set_key(PATHS["env"], "MCP_SERVER_URLS", json.dumps(urls))

        # Per-agent MCP URLs
        agent_mcps = conf.get("agent_mcps", {})

        # mateclaw_agent: defaults to all configured MCPs
        mateclaw_names = agent_mcps.get("mateclaw_agent", list(urls.keys()))
        mateclaw_urls = {k: v for k, v in urls.items() if k in mateclaw_names}
        set_key(PATHS["env"], "MATECLAW_AGENT_MCP_URLS", json.dumps(mateclaw_urls))

        # External agents with mcp_configurable: write their extra MCP env vars
        for ext_name, ext_agent in conf.get("external_agents", {}).items():
            if not ext_agent.get("mcp_configurable"):
                continue
            agent_key = ext_name.replace("-", "_")
            # Use mcp_env_var from manifest if available, else derive from name
            env_var = ext_agent.get("mcp_env_var") or (agent_key.upper() + "_MCP_URLS")
            selected = agent_mcps.get(agent_key, [])
            extra_urls = {k: v for k, v in urls.items() if k in selected}
            set_key(PATHS["env"], env_var, json.dumps(extra_urls))
            print(f"[MCP] Wrote {env_var}: {list(extra_urls.keys())}")

        # Important: Allow a small window for disk sync and reload env
        time.sleep(0.5)
        load_dotenv(PATHS["env"], override=True)

    @staticmethod
    def update_external_agents_env():
        """Serialize enabled external_agents into EXTERNAL_AGENTS_CONFIG env var."""
        conf = ConfigManager.get_config()
        agents_config = {}
        for name, agent in conf.get("external_agents", {}).items():
            if agent.get("enabled", True) and agent.get("a2a_url"):
                agents_config[name] = {
                    "a2a_url": agent["a2a_url"],
                    "description": agent.get("description", ""),
                }
        set_key(PATHS["env"], "EXTERNAL_AGENTS_CONFIG", json.dumps(agents_config))
        load_dotenv(PATHS["env"], override=True)

class AuthManager:
    SESSION_TOKEN = secrets.token_hex(16)

    @staticmethod
    def hash_password(password: str, salt: Optional[str] = None):
        salt = salt or secrets.token_hex(8)
        return hashlib.sha256((password + salt).encode()).hexdigest(), salt

    @staticmethod
    def save_auth(username, password):
        hashed, salt = AuthManager.hash_password(password)
        os.makedirs(os.path.dirname(PATHS["auth"]), exist_ok=True)
        with open(PATHS["auth"], "w") as f:
            json.dump({"username": username, "hashed": hashed, "salt": salt}, f)

    @staticmethod
    def get_auth():
        if os.path.exists(PATHS["auth"]):
            try:
                with open(PATHS["auth"], "r") as f:
                    return json.load(f)
            except: pass
        return None

    @staticmethod
    def verify_token(authorization: str = Header(None)):
        if authorization != f"Bearer {AuthManager.SESSION_TOKEN}":
            raise HTTPException(status_code=401, detail="Unauthorized")
        return True

class DockerManager:
    @staticmethod
    def get_cmd():
        try:
            subprocess.run(["docker", "compose", "version"], capture_output=True, check=True)
            return ["docker", "compose"]
        except: return ["docker-compose"]

    @staticmethod
    def get_compose_cwd(compose_file: str) -> str:
        mate_dir = os.path.join(_project_root, ".mateclaw")
        if os.path.exists(os.path.join(mate_dir, compose_file)):
            return mate_dir
        return _project_root

    @staticmethod
    def run_action(service: str, action: str):
        # Refresh process environment before calling docker
        load_dotenv(PATHS["env"], override=True)
        conf = ConfigManager.get_config()
        compose_file = "docker-compose.local.yaml" if conf.get("mode") == "local" else "docker-compose.yaml"
        compose_cwd = DockerManager.get_compose_cwd(compose_file)
        
        # Determine services to act upon
        target_services = [service]
        if service == "mateclaw-agent" and action == "restart":
            # If agent restarts, also restart all bots to reset sessions
            for p in conf.get("channels", []):
                bot_service = f"bot-{'telegram' if p=='tg' else 'discord' if p=='dc' else 'line'}"
                target_services.append(bot_service)

        for s in target_services:
            if action == "restart":
                # Force stop then start with recreate to ensure environment variables are fresh
                console.print(f"Force-restarting {s} to reload environment and reset sessions...")
                stop_cmd = DockerManager.get_cmd() + ["-f", compose_file, "stop", s]
                subprocess.run(stop_cmd, check=False, cwd=compose_cwd)
                
                up_cmd = DockerManager.get_cmd() + ["-f", compose_file, "up", "-d", "--force-recreate", s]
                subprocess.run(up_cmd, check=True, cwd=compose_cwd)
            elif action == "start":
                up_cmd = DockerManager.get_cmd() + ["-f", compose_file, "up", "-d", s]
                subprocess.run(up_cmd, check=True, cwd=compose_cwd)
            else:
                cmd = DockerManager.get_cmd() + ["-f", compose_file, action, s]
                subprocess.run(cmd, check=True, cwd=compose_cwd)

class DatabaseManager:
    @staticmethod
    def get_engine():
        load_dotenv(PATHS["env"])
        uri = os.getenv("ADK_SESSION_SERVICE_URI", "")
        if not uri: return None
        
        # Connection cleanup for local access
        if "postgres:5432" in uri: uri = uri.replace("postgres:5432", "localhost:5432")
        uri = uri.replace("postgresql+asyncpg://", "postgresql://").replace("sqlite+aiosqlite://", "sqlite://")
        
        if "sqlite:///app/data" in uri and ConfigManager.get_config().get("mode") == "local":
            uri = PATHS["db_local"]
            
        try: return create_engine(uri)
        except: return None

    @staticmethod
    def get_table_counts():
        engine = DatabaseManager.get_engine()
        if not engine: return {}
        counts = {}
        tables = ["events", "sessions", "user_states", "reminders", "identity_maps", "file_tasks", "user_contacts", "contacts"]
        with engine.connect() as conn:
            for t in tables:
                try: counts[t] = conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
                except: counts[t] = "N/A"
        return counts

# --- FastAPI App ---
server = FastAPI(title="Mateclaw Dashboard")
server.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
server.mount("/css", StaticFiles(directory=os.path.join(PATHS["frontend"], "css")), name="css")
server.mount("/js", StaticFiles(directory=os.path.join(PATHS["frontend"], "js")), name="js")
server.mount("/views", StaticFiles(directory=os.path.join(PATHS["frontend"], "views")), name="views")

@server.get("/health")
def health():
    return {"status": "ok", "version": VERSION}

@server.get("/api/check-setup")
def check_setup():
    return {"is_setup": AuthManager.get_auth() is not None}

@server.post("/api/setup")
def setup_account(req: SetupRequest):
    if AuthManager.get_auth() is not None:
        raise HTTPException(status_code=400, detail="Account already exists")
    AuthManager.save_auth(req.username, req.password)
    return {"status": "success"}

@server.post("/api/login")
def login(req: LoginRequest):
    auth = AuthManager.get_auth()
    if auth and req.username == auth["username"] and AuthManager.hash_password(req.password, auth["salt"])[0] == auth["hashed"]:
        return {"token": AuthManager.SESSION_TOKEN}
    raise HTTPException(status_code=401, detail="Invalid credentials")

@server.get("/api/status")
def get_status(auth: bool = Depends(AuthManager.verify_token)):
    res = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}"], capture_output=True, text=True)
    return [{"name": p[0], "status": p[1], "image": p[2]} for line in res.stdout.splitlines() if (p := line.split("\t")) and len(p) >= 3 and any(k in p[0] for k in ["mateclaw", "mcp", "bot", "postgres", "gpt-vis"])]

@server.get("/api/os-stats")
def get_os_stats(auth: bool = Depends(AuthManager.verify_token)):
    return {"cpu": psutil.cpu_percent(), "memory": psutil.virtual_memory().percent, "disk": psutil.disk_usage('/').percent, "uptime": int(time.time() - psutil.boot_time())}

@server.get("/api/license")
def get_license(auth: bool = Depends(AuthManager.verify_token)):
    sys.path.insert(0, _project_root)
    from src.core.license import LicenseManager, OSS_LIMITS
    mate_dir = os.path.join(_project_root, ".mateclaw")
    license_path = os.path.join(mate_dir, "mateclaw-license.yaml")
    def _monthly_used() -> int:
        try:
            engine = DatabaseManager.get_engine()
            if not engine:
                return 0
            from datetime import date
            month_start = date.today().replace(day=1).isoformat()
            with engine.connect() as conn:
                result = conn.execute(
                    text("SELECT COUNT(*) FROM task_logs WHERE status = 'done' AND created_at >= :ms"),
                    {"ms": month_start}
                )
                return result.scalar() or 0
        except Exception:
            return 0

    def _current_usage() -> dict:
        conf = ConfigManager.get_config()
        # extra_mcp: count external MCPs that are enabled
        ext = conf.get("external_mcp", {})
        used_mcp = sum(1 for v in ext.values() if (v.get("enabled", True) if isinstance(v, dict) else True))
        # users: count rows in user_contacts
        used_users = 0
        try:
            engine = DatabaseManager.get_engine()
            if engine:
                with engine.connect() as conn:
                    result = conn.execute(text("SELECT COUNT(*) FROM identity_maps WHERE is_approved = true"))
                    used_users = result.scalar() or 0
        except Exception:
            pass
        # channels: count explicitly enabled channels in config
        used_channels = len(conf.get("channels", []))
        # apis and skills: count rows in their tables
        used_apis = 0
        used_skills = 0
        try:
            engine = DatabaseManager.get_engine()
            if engine:
                with engine.connect() as conn:
                    used_apis   = conn.execute(text("SELECT COUNT(*) FROM api_configs")).scalar() or 0
                    used_skills = conn.execute(text("SELECT COUNT(*) FROM skill_configs")).scalar() or 0
        except Exception:
            pass
        return {
            "monthly_executions": _monthly_used(),
            "extra_mcp":          used_mcp,
            "users":              used_users,
            "enabled_channels":   used_channels,
            "apis":               used_apis,
            "skills":             used_skills,
        }

    try:
        info = LicenseManager.load(license_path)
        if info:
            return {
                "plan":         info.plan,
                "issued_to":    info.issued_to,
                "expires_at":   str(info.expires_at) if info.expires_at else None,
                "is_expired":   info.is_expired,
                "limits": {
                    "extra_mcp":          info.extra_mcp,
                    "monthly_executions": info.monthly_executions,
                    "max_users":          info.max_users,
                    "enabled_channels":   info.enabled_channels,
                    "max_apis":           info.max_apis,
                    "max_skills":         info.max_skills,
                },
                "usage": _current_usage(),
            }
    except ValueError as e:
        return {"plan": "invalid", "error": str(e)}
    return {
        "plan":      "oss",
        "issued_to": None,
        "expires_at": None,
        "is_expired": False,
        "limits":    OSS_LIMITS,
        "usage":     _current_usage(),
    }

@server.post("/api/services/{service}/action")
def service_action(service: str, req: ServiceActionRequest, auth: bool = Depends(AuthManager.verify_token)):
    if req.action == "stop" and service in ["mcp-mateclaw", "mateclaw-agent", "postgres"]:
        raise HTTPException(status_code=400, detail="Cannot stop core service.")
    try:
        DockerManager.run_action(service, req.action)
        # Sync channels list when bot services are started/stopped
        bot_map = {"bot-telegram": "tg", "bot-discord": "dc", "bot-line": "line"}
        if service in bot_map:
            ch_id = bot_map[service]
            conf = ConfigManager.get_config()
            channels = conf.get("channels", [])
            if req.action == "start" and ch_id not in channels:
                channels.append(ch_id)
                conf["channels"] = channels
                ConfigManager.save_config(conf)
            elif req.action == "stop" and ch_id in channels:
                conf["channels"] = [c for c in channels if c != ch_id]
                ConfigManager.save_config(conf)
        return {"status": "success"}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@server.get("/api/config")
def get_api_config(auth: bool = Depends(AuthManager.verify_token)):
    conf = ConfigManager.get_config()
    load_dotenv(PATHS["env"])
    # Sync environment tokens to the config object for UI
    if "gateways_config" not in conf: conf["gateways_config"] = {}
    tokens = {"tg": "TELEGRAM_BOT_TOKEN", "dc": "DISCORD_BOT_TOKEN", "line": "LINE_CHANNEL_ACCESS_TOKEN"}
    for k, v in tokens.items():
        if t := os.getenv(v):
            conf["gateways_config"].setdefault(k, {})["token"] = t
    return conf

@server.post("/api/config/require-approval")
def set_require_approval(req: dict, auth: bool = Depends(AuthManager.verify_token)):
    # Approval gate is an Enterprise-only feature
    mate_dir = os.path.join(_project_root, ".mateclaw")
    license_path = os.path.join(mate_dir, "mateclaw-license.yaml")
    if not os.path.exists(license_path):
        raise HTTPException(status_code=403, detail="User approval gate is an Enterprise feature. OSS installations cannot enable this setting.")
    conf = ConfigManager.get_config()
    conf["require_approval"] = bool(req.get("enabled", True))
    ConfigManager.save_config(conf)
    return {"status": "ok", "require_approval": conf["require_approval"]}

@server.post("/api/config/coding-agent")
def set_coding_agent(req: dict, auth: bool = Depends(AuthManager.verify_token)):
    conf = ConfigManager.get_config()
    enabled_changed = "enabled" in req
    if enabled_changed:
        enabled = bool(req["enabled"])
        conf["coding_agent_enabled"] = enabled
        # Keep external_agents in sync
        coding_a2a_url = os.getenv("CODING_A2A_INTERNAL_URL", "http://coding-agent:8081")
        conf.setdefault("external_agents", {}).setdefault("coding-agent", {
            "type": "github",
            "a2a_url": coding_a2a_url,
            "description": "寫程式並執行來解決需要計算、資料處理或程式邏輯的問題，回傳執行結果與產生的檔案路徑。",
            "container_names": ["coding-agent", "mcp-coding"],
        })["enabled"] = enabled
        conf["external_agents"]["coding-agent"]["a2a_url"] = coding_a2a_url
    ConfigManager.save_config(conf)
    ConfigManager.update_external_agents_env()

    if enabled_changed and conf.get("mode") != "local":
        enabled = conf["coding_agent_enabled"]
        def _apply_docker():
            load_dotenv(PATHS["env"], override=True)
            compose_file = "docker-compose.yaml"
            compose_cwd = DockerManager.get_compose_cwd(compose_file)
            cmd_base = DockerManager.get_cmd() + ["-f", compose_file]
            if enabled:
                subprocess.run(
                    cmd_base + ["--profile", "coding", "up", "-d", "mcp-coding", "coding-agent"],
                    check=False, cwd=compose_cwd
                )
            else:
                subprocess.run(
                    cmd_base + ["stop", "coding-agent", "mcp-coding"],
                    check=False, cwd=compose_cwd
                )
            DockerManager.run_action("mateclaw-agent", "restart")
        threading.Thread(target=_apply_docker, daemon=True).start()

    return {"status": "ok", "coding_agent_enabled": conf["coding_agent_enabled"]}

@server.post("/api/gateways")
def save_gateway(req: GatewayUpdateRequest, auth: bool = Depends(AuthManager.verify_token)):
    token_env_map = {"tg": "TELEGRAM_BOT_TOKEN", "dc": "DISCORD_BOT_TOKEN", "line": "LINE_CHANNEL_ACCESS_TOKEN"}
    secret_env_map = {"line": "LINE_CHANNEL_SECRET"}
    p = req.platform
    if p not in token_env_map:
        raise HTTPException(status_code=400, detail="Unknown platform.")
    # Save token to .env
    if token := req.config.get("token"):
        set_key(PATHS["env"], token_env_map[p], token)
    if secret := req.config.get("secret"):
        if p in secret_env_map:
            set_key(PATHS["env"], secret_env_map[p], secret)
    # Add to channels if not already present
    conf = ConfigManager.get_config()
    if p not in conf.get("channels", []):
        conf.setdefault("channels", []).append(p)
    ConfigManager.save_config(conf)
    return {"status": "ok"}

@server.post("/api/mcp")
def add_mcp(req: AddMCPRequest, auth: bool = Depends(AuthManager.verify_token)):
    conf = ConfigManager.get_config()
    if req.is_external:
        # Accept Dive-format object or legacy plain URL string
        if req.config and isinstance(req.config, dict) and "url" in req.config:
            dive_obj = {
                "url":       req.config.get("url", req.url or ""),
                "transport": req.config.get("transport", "streamable"),
                "enabled":   req.config.get("enabled", True),
                "headers":   req.config.get("headers", {}),
            }
            if not dive_obj["url"]: raise HTTPException(status_code=400, detail="External URL missing.")
            conf["external_mcp"][req.name] = dive_obj
        else:
            url = req.url
            if not url: raise HTTPException(status_code=400, detail="External URL missing.")
            conf["external_mcp"][req.name] = {
                "url":       url,
                "transport": "sse" if "/sse" in url else "streamable",
                "enabled":   True,
                "headers":   {},
            }
        if req.name in conf["mcp"]: conf["mcp"].remove(req.name)
    else:
        if req.name not in conf["mcp"]: conf["mcp"].append(req.name)
        if req.name in conf["external_mcp"]: del conf["external_mcp"][req.name]
    
    ConfigManager.save_config(conf)
    if req.config and req.name == "mateclaw":
        path = os.path.join("mcp_servers", "mateclaw", "server.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f: json.dump(req.config, f, indent=2)
        
    ConfigManager.update_mcp_urls()
    return {"status": "success"}

@server.get("/api/mcp/{name}/config")
def get_mcp_config(name: str, auth: bool = Depends(AuthManager.verify_token)):
    conf = ConfigManager.get_config()
    # External MCP: return Dive-format object
    if name in conf.get("external_mcp", {}):
        val = conf["external_mcp"][name]
        if isinstance(val, str):
            return {"url": val, "transport": "sse" if "/sse" in val else "streamable", "enabled": True, "headers": {}}
        return val
    # Built-in MCP: return server.json (only mateclaw core MCP has a local config)
    if name == "mateclaw":
        path = os.path.join("mcp_servers", "mateclaw", "server.json")
        if os.path.exists(path):
            with open(path, "r") as f: return json.load(f)
    return {"name": name, "description": "No config found."}

@server.post("/api/mcp/{name}/config")
async def update_mcp_config(name: str, request: Request, auth: bool = Depends(AuthManager.verify_token)):
    body = await request.json()
    conf = ConfigManager.get_config()
    if name in conf.get("external_mcp", {}):
        existing = conf["external_mcp"][name]
        if isinstance(existing, str):
            existing = {"url": existing, "transport": "sse" if "/sse" in existing else "streamable", "enabled": True, "headers": {}}
        existing.update({k: v for k, v in body.items() if k in ("url", "transport", "enabled", "headers", "description")})
        conf["external_mcp"][name] = existing
        ConfigManager.save_config(conf)
        ConfigManager.update_mcp_urls()
    elif name == "mateclaw":
        # Built-in MCP: update server.json (only mateclaw core MCP has a local config)
        path = os.path.join("mcp_servers", "mateclaw", "server.json")
        existing = {}
        if os.path.exists(path):
            with open(path, "r") as f: existing = json.load(f)
        existing.update(body)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f: json.dump(existing, f, indent=2)
    return {"status": "success"}

@server.delete("/api/mcp/{name}")
def delete_mcp(name: str, auth: bool = Depends(AuthManager.verify_token)):
    if name == "mateclaw": raise HTTPException(status_code=400, detail="Cannot delete core MCP.")
    conf = ConfigManager.get_config()
    if name in conf["mcp"]: conf["mcp"].remove(name)
    if name in conf["external_mcp"]: del conf["external_mcp"][name]
    ConfigManager.save_config(conf)
    ConfigManager.update_mcp_urls()
    return {"status": "success"}

@server.get("/api/chat/sessions")
def get_chat_sessions(auth: bool = Depends(AuthManager.verify_token)):
    engine = DatabaseManager.get_engine()
    if not engine: return []
    try:
        with engine.connect() as conn:
            res = conn.execute(text('SELECT id, user_id, app_name, "update_time" FROM sessions ORDER BY "update_time" DESC'))
            return [dict(row._mapping) for row in res]
    except Exception as e:
        import traceback; traceback.print_exc()
        return []

@server.get("/api/chat/history/{session_id}")
def get_chat_history(session_id: str, auth: bool = Depends(AuthManager.verify_token)):
    engine = DatabaseManager.get_engine()
    if not engine: return []
    try:
        with engine.connect() as conn:
            res = conn.execute(text('SELECT event_data, "timestamp" FROM events WHERE session_id = :sid ORDER BY "timestamp" ASC'), {"sid": session_id})
            rows = []
            for row in res:
                ed = row[0]
                if isinstance(ed, str): ed = json.loads(ed)
                ts = row[1].timestamp() if isinstance(row[1], datetime) else float(row[1])
                rows.append({"event_data": ed, "timestamp": ts})
            return rows
    except Exception as e:
        import traceback; traceback.print_exc()
        return []

@server.get("/api/db/{table}")
def get_db_table_data(table: str, auth: bool = Depends(AuthManager.verify_token)):
    engine = DatabaseManager.get_engine()
    if not engine: return []
    queries = {
        "identities": "SELECT session_id, hashed_id, real_id, created_at FROM identity_maps ORDER BY created_at DESC",
        "profiles": "SELECT user_id, chinese_name, job_title, company_name, personal_email, mobile_phone, employee_id, note FROM user_contacts",
        "reminders": "SELECT id, cron, channel, recipient, status, prompt FROM reminders ORDER BY id DESC",
        "events": "SELECT id, session_id, event_data, timestamp FROM events ORDER BY timestamp DESC LIMIT 200",
        "user_states": "SELECT app_name, user_id, state, update_time FROM user_states"
    }
    if table not in queries: return []
    try:
        with engine.connect() as conn:
            res = conn.execute(text(queries[table]))
            rows = []
            for row in res:
                d = dict(row._mapping)
                for k, v in d.items():
                    if isinstance(v, datetime):
                        # Force UTC marker 'Z' so the frontend browser can convert to local timezone
                        if v.tzinfo is None:
                            d[k] = v.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
                        else:
                            d[k] = v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                if table == "events" and "event_data" in d:
                    ed = d["event_data"]
                    if isinstance(ed, str): ed = json.loads(ed)
                    d["author"] = ed.get("author", "unknown")
                    raw_parts = ed.get("content", {}).get("parts", [])
                    simplified = []
                    for p in raw_parts:
                        if "text" in p:
                            simplified.append({"type": "text", "text": p["text"][:800]})
                        elif "functionCall" in p:
                            fc = p["functionCall"]
                            simplified.append({"type": "call", "name": fc.get("name", ""), "args": fc.get("args", {})})
                        elif "functionResponse" in p:
                            fr = p["functionResponse"]
                            resp = fr.get("response", {})
                            data = resp.get("structuredContent") or resp.get("content", "")
                            simplified.append({"type": "result", "name": fr.get("name", ""), "data": data})
                    d["content"] = json.dumps(simplified, ensure_ascii=False) if simplified else json.dumps([{"type": "text", "text": "(Empty)"}])
                rows.append(d)
            return rows
    except: return []

@server.get("/api/users")
def get_users(auth: bool = Depends(AuthManager.verify_token)):
    """Returns user profiles (no approval logic here)."""
    engine = DatabaseManager.get_engine()
    if not engine: return []
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT user_id, chinese_name, english_name, job_title, company_name, "
                "personal_email, mobile_phone FROM user_contacts ORDER BY chinese_name"
            ))
            return [dict(r._mapping) for r in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@server.get("/api/identities")
def get_identities(auth: bool = Depends(AuthManager.verify_token)):
    """Returns identity map entries joined with profile name."""
    engine = DatabaseManager.get_engine()
    if not engine: return []
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT i.session_id, i.hashed_id, i.real_id, i.is_approved, i.created_at,
                       COALESCE(u.chinese_name, u.english_name) AS name
                FROM identity_maps i
                LEFT JOIN user_contacts u ON u.user_id = i.hashed_id
                ORDER BY i.created_at DESC
            """))
            result = []
            for row in rows:
                d = dict(row._mapping)
                if isinstance(d.get("created_at"), datetime):
                    d["created_at"] = d["created_at"].replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
                result.append(d)
            return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@server.post("/api/identities/{session_id}/approve")
def approve_identity(session_id: str, auth: bool = Depends(AuthManager.verify_token)):
    engine = DatabaseManager.get_engine()
    if not engine: raise HTTPException(status_code=500, detail="Database unavailable")
    with engine.connect() as conn:
        conn.execute(text("UPDATE identity_maps SET is_approved = true WHERE session_id = :sid"), {"sid": session_id})
        conn.commit()
    return {"status": "approved"}

@server.post("/api/identities/{session_id}/revoke")
def revoke_identity(session_id: str, auth: bool = Depends(AuthManager.verify_token)):
    engine = DatabaseManager.get_engine()
    if not engine: raise HTTPException(status_code=500, detail="Database unavailable")
    with engine.connect() as conn:
        conn.execute(text("UPDATE identity_maps SET is_approved = false WHERE session_id = :sid"), {"sid": session_id})
        conn.commit()
    return {"status": "revoked"}

@server.delete("/api/identities/{session_id}")
def delete_identity(session_id: str, auth: bool = Depends(AuthManager.verify_token)):
    engine = DatabaseManager.get_engine()
    if not engine: raise HTTPException(status_code=500, detail="Database unavailable")
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM identity_maps WHERE session_id = :sid"), {"sid": session_id})
        conn.commit()
    return {"status": "deleted"}

@server.delete("/api/memory/user_states")
def delete_user_state(app_name: str, user_id: str, auth: bool = Depends(AuthManager.verify_token)):
    engine = DatabaseManager.get_engine()
    if not engine: raise HTTPException(status_code=500, detail="Database unavailable")
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM user_states WHERE app_name = :a AND user_id = :u"), {"a": app_name, "u": user_id})
        conn.commit()
    return {"status": "deleted"}

@server.delete("/api/users/{user_id}")
def delete_user(user_id: str, auth: bool = Depends(AuthManager.verify_token)):
    engine = DatabaseManager.get_engine()
    if not engine: raise HTTPException(status_code=500, detail="Database unavailable")
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM user_contacts WHERE user_id = :uid"), {"uid": user_id})
        conn.commit()
    return {"status": "deleted"}

@server.post("/api/reminders")
def create_reminder(req: AddReminderRequest, auth: bool = Depends(AuthManager.verify_token)):
    engine = DatabaseManager.get_engine()
    if not engine: raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        rid = str(uuid.uuid4())
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO reminders (id, cron, channel, recipient, status, prompt, app_name, subject, body, user_id, session_id) 
                VALUES (:id, :cron, :channel, :recipient, :status, :prompt, :app_name, :subject, :body, :user_id, :session_id)
            """), {
                "id": rid,
                "cron": req.cron,
                "channel": req.channel,
                "recipient": req.recipient,
                "status": "scheduled",
                "prompt": req.prompt,
                "app_name": "mateclaw_agent",
                "subject": "Scheduled Message",
                "body": req.prompt,
                "user_id": "dashboard-user",
                "session_id": "dashboard-manual"
            })
            conn.commit()
        return {"status": "success", "id": rid}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@server.delete("/api/reminders/{reminder_id}")
def delete_reminder(reminder_id: str, auth: bool = Depends(AuthManager.verify_token)):
    engine = DatabaseManager.get_engine()
    if not engine: raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM reminders WHERE id = :rid"), {"rid": reminder_id})
            conn.commit()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@server.put("/api/reminders/{reminder_id}")
def update_reminder(reminder_id: str, req: AddReminderRequest, auth: bool = Depends(AuthManager.verify_token)):
    engine = DatabaseManager.get_engine()
    if not engine: raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                UPDATE reminders SET 
                    cron = :cron, 
                    channel = :channel, 
                    recipient = :recipient, 
                    prompt = :prompt,
                    status = :status
                WHERE id = :id
            """), {
                "id": reminder_id,
                "cron": req.cron,
                "channel": req.channel,
                "recipient": req.recipient,
                "prompt": req.prompt,
                "status": "scheduled"
            })
            conn.commit()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@server.get("/api/tasks")
def get_tasks(auth: bool = Depends(AuthManager.verify_token)):
    engine = DatabaseManager.get_engine()
    if not engine: return []
    try:
        with engine.connect() as conn:
            # Query all tasks
            res = conn.execute(text("SELECT * FROM tasks ORDER BY created_at DESC NULLS LAST"))
            rows = []
            for row in res:
                d = dict(row._mapping)
                for k, v in d.items():
                    if isinstance(v, datetime):
                        # Force UTC marker 'Z'
                        if v.tzinfo is None:
                            d[k] = v.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
                        else:
                            d[k] = v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                    elif v is None:
                        d[k] = None
                rows.append(d)
            return rows
    except Exception as e:
        import logging; logging.getLogger(__name__).error(f"get_tasks error: {e}")
        return []

@server.post("/api/tasks")
def create_task(req: TaskCreateRequest, auth: bool = Depends(AuthManager.verify_token)):
    engine = DatabaseManager.get_engine()
    if not engine: raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        tid = str(uuid.uuid4())
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO tasks (id, title, spec, cron, channel, recipient, status, user_id, session_id, created_at, updated_at) 
                VALUES (:id, :title, :spec, :cron, :channel, :recipient, :status, :user_id, :session_id, :now, :now)
            """), {
                "id": tid,
                "title": req.title,
                "spec": req.spec,
                "cron": req.cron,
                "channel": req.channel,
                "recipient": req.recipient,
                "status": "backlog",
                "user_id": "dashboard-user",
                "session_id": f"task-{tid[:8]}",
                "now": datetime.utcnow()
            })
            conn.commit()
        return {"status": "success", "id": tid}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@server.put("/api/tasks/{task_id}")
def update_task(task_id: str, req: TaskUpdateRequest, auth: bool = Depends(AuthManager.verify_token)):
    engine = DatabaseManager.get_engine()
    if not engine: raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        update_data = req.dict(exclude_unset=True)
        if not update_data: return {"status": "no changes"}
        allowed_fields = {"title", "spec", "status", "cron", "channel", "recipient", "result"}
        update_data = {k: v for k, v in update_data.items() if k in allowed_fields}
        if not update_data: return {"status": "no changes"}
        update_data["id"] = task_id
        update_data["now"] = datetime.utcnow()
        set_clauses = [f"{k} = :{k}" for k in update_data.keys() if k not in ["id", "now"]]
        set_clauses.append("updated_at = :now")
        
        with engine.connect() as conn:
            conn.execute(text(f"UPDATE tasks SET {', '.join(set_clauses)} WHERE id = :id"), update_data)
            conn.commit()
        return {"status": "success"}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@server.delete("/api/tasks/{task_id}")
def delete_task(task_id: str, auth: bool = Depends(AuthManager.verify_token)):
    engine = DatabaseManager.get_engine()
    if not engine: raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM tasks WHERE id = :id"), {"id": task_id})
            conn.commit()
        return {"status": "success"}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@server.post("/api/tasks/{task_id}/play")
async def play_task(task_id: str, auth: bool = Depends(AuthManager.verify_token)):
    engine = DatabaseManager.get_engine()
    if not engine: raise HTTPException(status_code=500, detail="Database connection failed")
    
    try:
        with engine.connect() as conn:
            # Set status to 'queued' so the mcp-reminder background worker picks it up
            conn.execute(text("UPDATE tasks SET status = 'queued', updated_at = :now WHERE id = :id"), 
                         {"id": task_id, "now": datetime.utcnow()})
            conn.commit()
        return {"status": "triggered"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@server.get("/api/tasks/{task_id}/logs")
def get_task_logs(task_id: str, auth: bool = Depends(AuthManager.verify_token)):
    engine = DatabaseManager.get_engine()
    if not engine: return []
    try:
        with engine.connect() as conn:
            res = conn.execute(text("SELECT id, status, output, created_at FROM task_logs WHERE task_id = :id ORDER BY created_at DESC LIMIT 50"), {"id": task_id})
            rows = []
            for row in res:
                d = dict(row._mapping)
                if isinstance(d.get('created_at'), datetime):
                    d['created_at'] = d['created_at'].replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
                rows.append(d)
            return rows
    except Exception as e: return []

@server.get("/api/apis")
def list_api_configs(auth: bool = Depends(AuthManager.verify_token)):
    engine = DatabaseManager.get_engine()
    if not engine: return []
    try:
        with engine.connect() as conn:
            res = conn.execute(text("SELECT id, name, url, method, headers_encrypted, description, user_id, agent_ids, is_active, created_at FROM api_configs ORDER BY created_at DESC"))
            rows = []
            for row in res:
                d = dict(row._mapping)
                # Expose only header key names, not values
                if d.get("headers_encrypted"):
                    try:
                        h = decrypt_headers(d["headers_encrypted"])
                        d["header_keys"] = list(h.keys())
                    except Exception:
                        d["header_keys"] = []
                else:
                    d["header_keys"] = []
                del d["headers_encrypted"]
                if isinstance(d.get("created_at"), datetime):
                    d["created_at"] = d["created_at"].replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
                rows.append(d)
            return rows
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@server.post("/api/apis")
def create_api_config(req: ApiConfigCreateRequest, auth: bool = Depends(AuthManager.verify_token)):
    if not is_safe_url(req.url):
        raise HTTPException(status_code=400, detail="URL resolves to a private/reserved IP address (SSRF protection).")
    engine = DatabaseManager.get_engine()
    if not engine: raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        sys.path.insert(0, _project_root)
        from src.core.license import LicenseManager
        with Session(engine) as _s:
            LicenseManager.check_api_limit(_s)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    try:
        new_id = str(uuid.uuid4())
        headers_enc = encrypt_headers(req.headers) if req.headers else None
        effective_user_id = req.user_id or "__global__"
        effective_agent_ids = req.agent_ids or "__all__"
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO api_configs (id, name, url, method, headers_encrypted, description, user_id, agent_ids, is_active, created_at, updated_at)
                VALUES (:id, :name, :url, :method, :headers_encrypted, :description, :user_id, :agent_ids, :is_active, :now, :now)
            """), {
                "id": new_id, "name": req.name, "url": req.url, "method": req.method.upper(),
                "headers_encrypted": headers_enc, "description": req.description,
                "user_id": effective_user_id, "agent_ids": effective_agent_ids,
                "is_active": True, "now": datetime.utcnow()
            })
            conn.commit()
        return {"status": "success", "id": new_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@server.put("/api/apis/{api_id}")
def update_api_config(api_id: str, req: ApiConfigUpdateRequest, auth: bool = Depends(AuthManager.verify_token)):
    if req.url and not is_safe_url(req.url):
        raise HTTPException(status_code=400, detail="URL resolves to a private/reserved IP address (SSRF protection).")
    engine = DatabaseManager.get_engine()
    if not engine: raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        updates: Dict[str, Any] = {"id": api_id, "now": datetime.utcnow()}
        if req.name is not None: updates["name"] = req.name
        if req.url is not None: updates["url"] = req.url
        if req.method is not None: updates["method"] = req.method.upper()
        if req.headers is not None: updates["headers_encrypted"] = encrypt_headers(req.headers)
        if req.description is not None: updates["description"] = req.description
        if req.is_active is not None: updates["is_active"] = req.is_active
        if req.agent_ids is not None: updates["agent_ids"] = req.agent_ids
        set_clauses = [f"{k} = :{k}" for k in updates if k not in ("id", "now")]
        set_clauses.append("updated_at = :now")
        with engine.connect() as conn:
            conn.execute(text(f"UPDATE api_configs SET {', '.join(set_clauses)} WHERE id = :id"), updates)
            conn.commit()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@server.delete("/api/apis/{api_id}")
def delete_api_config(api_id: str, auth: bool = Depends(AuthManager.verify_token)):
    engine = DatabaseManager.get_engine()
    if not engine: raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM api_configs WHERE id = :id"), {"id": api_id})
            conn.commit()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@server.get("/api/skills")
def list_skill_configs(auth: bool = Depends(AuthManager.verify_token)):
    engine = DatabaseManager.get_engine()
    if not engine: return []
    try:
        with engine.connect() as conn:
            res = conn.execute(text("SELECT id, name, description, tags, usage, user_id, agent_ids, is_active, created_at FROM skill_configs ORDER BY created_at DESC"))
            rows = []
            for row in res:
                d = dict(row._mapping)
                if isinstance(d.get("created_at"), datetime):
                    d["created_at"] = d["created_at"].replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
                rows.append(d)
            return rows
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@server.post("/api/skills")
def create_skill_config(req: SkillConfigCreateRequest, auth: bool = Depends(AuthManager.verify_token)):
    engine = DatabaseManager.get_engine()
    if not engine: raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        sys.path.insert(0, _project_root)
        from src.core.license import LicenseManager
        with Session(engine) as _s:
            LicenseManager.check_skill_limit(_s)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    try:
        new_id = str(uuid.uuid4())
        effective_user_id = req.user_id or "__global__"
        effective_agent_ids = req.agent_ids or "__all__"
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO skill_configs (id, name, description, tags, usage, user_id, agent_ids, is_active, created_at, updated_at)
                VALUES (:id, :name, :description, :tags, :usage, :user_id, :agent_ids, :is_active, :now, :now)
            """), {
                "id": new_id, "name": req.name, "description": req.description,
                "tags": req.tags, "usage": req.usage, "user_id": effective_user_id,
                "agent_ids": effective_agent_ids, "is_active": True, "now": datetime.utcnow()
            })
            conn.commit()
        return {"status": "success", "id": new_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@server.put("/api/skills/{skill_id}")
def update_skill_config(skill_id: str, req: SkillConfigUpdateRequest, auth: bool = Depends(AuthManager.verify_token)):
    engine = DatabaseManager.get_engine()
    if not engine: raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        updates = {}
        if req.name is not None: updates["name"] = req.name
        if req.description is not None: updates["description"] = req.description
        if req.tags is not None: updates["tags"] = req.tags
        if req.usage is not None: updates["usage"] = req.usage
        if req.is_active is not None: updates["is_active"] = req.is_active
        if req.agent_ids is not None: updates["agent_ids"] = req.agent_ids
        if not updates: return {"status": "success"}
        updates["updated_at"] = datetime.utcnow()
        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        updates["id"] = skill_id
        with engine.connect() as conn:
            conn.execute(text(f"UPDATE skill_configs SET {set_clause} WHERE id = :id"), updates)
            conn.commit()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@server.delete("/api/skills/{skill_id}")
def delete_skill_config(skill_id: str, auth: bool = Depends(AuthManager.verify_token)):
    engine = DatabaseManager.get_engine()
    if not engine: raise HTTPException(status_code=500, detail="Database connection failed")
    try:
        with engine.connect() as conn:
            conn.execute(text("DELETE FROM skill_configs WHERE id = :id"), {"id": skill_id})
            conn.commit()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@server.get("/api/agent-mcp-config")
def get_agent_mcp_config(auth: bool = Depends(AuthManager.verify_token)):
    conf = ConfigManager.get_config()
    all_mcp_names = list(conf.get("mcp", []))
    for name, val in conf.get("external_mcp", {}).items():
        enabled = val.get("enabled", True) if isinstance(val, dict) else True
        if enabled:
            all_mcp_names.append(name)

    agent_mcps = conf.get("agent_mcps", {})

    result_mcps = {
        "mateclaw_agent": agent_mcps.get("mateclaw_agent", all_mcp_names),
    }
    # Include github-type external agents (URL agents are not managed by Mateclaw)
    for name, agent in conf.get("external_agents", {}).items():
        if agent.get("type") == "github":
            agent_key = name.replace("-", "_")
            result_mcps[agent_key] = agent_mcps.get(agent_key, all_mcp_names)

    return {"available_mcps": all_mcp_names, "agent_mcps": result_mcps}

@server.post("/api/agent-mcp-config")
def update_agent_mcp_config(req: AgentMCPConfigRequest, auth: bool = Depends(AuthManager.verify_token)):
    conf = ConfigManager.get_config()
    agent_mcps = conf.get("agent_mcps", {})
    agent_mcps[req.agent_id] = req.mcps
    conf["agent_mcps"] = agent_mcps
    ConfigManager.save_config(conf)
    ConfigManager.update_mcp_urls()

    # Find if this is a github-type external agent (has its own compose fragment)
    agent_id_to_name = {n.replace("-", "_"): n for n in conf.get("external_agents", {})}
    ext_name = agent_id_to_name.get(req.agent_id)
    ext_agent_conf = conf.get("external_agents", {}).get(ext_name) if ext_name else None

    if ext_agent_conf and ext_agent_conf.get("type") == "github" and ext_agent_conf.get("fragment_path"):
        # Restart using the compose fragment so service definition is available
        main_compose = os.path.join(_project_root, ".mateclaw", "docker-compose.yaml")
        fragment_path = ext_agent_conf["fragment_path"]
        primary_service = ext_agent_conf.get("container_names", [ext_name])[0]
        def _restart_ext_agent():
            load_dotenv(PATHS["env"], override=True)
            stop_cmd = DockerManager.get_cmd() + ["-f", main_compose, "-f", fragment_path, "stop", primary_service]
            subprocess.run(stop_cmd, check=False, cwd=_project_root)
            up_cmd = DockerManager.get_cmd() + ["-f", main_compose, "-f", fragment_path, "up", "-d", "--force-recreate", primary_service]
            subprocess.run(up_cmd, check=False, cwd=_project_root)
            print(f"[MCP] Restarted external agent {ext_name} ({primary_service})")
        threading.Thread(target=_restart_ext_agent, daemon=True).start()
    else:
        # Internal agent (mateclaw-agent, coding-agent legacy, etc.)
        docker_name_map = {"mateclaw_agent": "mateclaw-agent", "coding_agent": "coding-agent"}
        docker_service = docker_name_map.get(req.agent_id)
        if docker_service:
            threading.Thread(target=DockerManager.run_action, args=(docker_service, "restart"), daemon=True).start()

    return {"status": "success", "agent_id": req.agent_id, "mcps": req.mcps}

@server.get("/api/agents")
def get_available_agents(auth: bool = Depends(AuthManager.verify_token)):
    try:
        with httpx.Client(timeout=5.0) as client:
            return client.get("http://localhost:18080/list-apps").json()
    except: return []

@server.get("/api/external-agents")
def list_external_agents(auth: bool = Depends(AuthManager.verify_token)):
    conf = ConfigManager.get_config()
    result = []
    for name, agent in conf.get("external_agents", {}).items():
        # Skip legacy migrated entries that have no public_port (old compose-managed coding-agent)
        if agent.get("type") == "github" and not agent.get("public_port"):
            continue
        health = False
        if agent.get("enabled"):
            # GitHub agents: use localhost:<public_port> (Docker internal URL not reachable from host)
            # URL agents: use a2a_url directly
            health_url = None
            if agent.get("type") == "github" and agent.get("public_port"):
                health_url = f"http://localhost:{agent['public_port']}/.well-known/agent.json"
            elif agent.get("type") == "url" and agent.get("a2a_url"):
                health_url = f"{agent['a2a_url']}/.well-known/agent.json"
            if health_url:
                try:
                    with httpx.Client(timeout=3.0) as client:
                        r = client.get(health_url)
                        health = r.status_code == 200
                except: pass
        result.append({
            "name": name,
            "type": agent.get("type", "url"),
            "a2a_url": agent.get("a2a_url", ""),
            "description": agent.get("description", ""),
            "enabled": agent.get("enabled", True),
            "mcp_configurable": agent.get("mcp_configurable", False),
            "health": health,
            "version": agent.get("version"),
            "container_names": agent.get("container_names", []),
        })
    return result

@server.post("/api/external-agents")
def add_external_agent(req: ExternalAgentAddRequest, auth: bool = Depends(AuthManager.verify_token)):
    name = req.name.strip().lower().replace(" ", "-")
    if not re.match(r'^[a-z0-9][a-z0-9_-]*$', name):
        raise HTTPException(400, "Agent name must be lowercase alphanumeric with hyphens/underscores.")
    conf = ConfigManager.get_config()
    if name in conf.get("external_agents", {}):
        raise HTTPException(400, f"Agent '{name}' already exists.")
    conf.setdefault("external_agents", {})[name] = {
        "type": "url",
        "a2a_url": req.a2a_url,
        "description": req.description or "",
        "enabled": True,
    }
    ConfigManager.save_config(conf)
    ConfigManager.update_external_agents_env()
    threading.Thread(target=DockerManager.run_action, args=("mateclaw-agent", "restart"), daemon=True).start()
    return {"status": "ok", "name": name}

@server.patch("/api/external-agents/{name}")
def update_external_agent(name: str, req: ExternalAgentUpdateRequest, auth: bool = Depends(AuthManager.verify_token)):
    conf = ConfigManager.get_config()
    if name not in conf.get("external_agents", {}):
        raise HTTPException(404, f"Agent '{name}' not found.")
    agent = conf["external_agents"][name]
    if req.a2a_url is not None: agent["a2a_url"] = req.a2a_url
    if req.description is not None: agent["description"] = req.description
    if req.enabled is not None:
        agent["enabled"] = req.enabled
        if name == "coding-agent":
            conf["coding_agent_enabled"] = req.enabled
    ConfigManager.save_config(conf)
    ConfigManager.update_external_agents_env()
    threading.Thread(target=DockerManager.run_action, args=("mateclaw-agent", "restart"), daemon=True).start()
    return {"status": "ok"}

@server.delete("/api/external-agents/{name}")
def remove_external_agent(name: str, auth: bool = Depends(AuthManager.verify_token)):
    conf = ConfigManager.get_config()
    if name not in conf.get("external_agents", {}):
        raise HTTPException(404, f"Agent '{name}' not found.")
    if conf["external_agents"][name].get("type") == "github":
        raise HTTPException(400, "GitHub-deployed agents must be removed via CLI: mateclaw agent remove")
    del conf["external_agents"][name]
    ConfigManager.save_config(conf)
    ConfigManager.update_external_agents_env()
    threading.Thread(target=DockerManager.run_action, args=("mateclaw-agent", "restart"), daemon=True).start()
    return {"status": "ok"}

@server.post("/api/proxy/run_sse")
async def proxy_run_sse(req: dict = Body(...), auth: bool = Depends(AuthManager.verify_token)):
    async def gen():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", "http://localhost:18080/run_sse", json=req) as r:
                async for line in r.aiter_lines():
                    if line: yield f"{line}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")

@server.post("/api/proxy/sessions/{app_name}/{user_id}/{session_id}")
async def proxy_create_session(app_name: str, user_id: str, session_id: str, auth: bool = Depends(AuthManager.verify_token)):
    async with httpx.AsyncClient() as client:
        res = await client.post(f"http://localhost:18080/apps/{app_name}/users/{user_id}/sessions/{session_id}", json={"state": {}})
        return res.json()

@server.get("/api/logs/{service}")
def get_service_logs(service: str, tail: int = 100, auth: bool = Depends(AuthManager.verify_token)):
    conf = ConfigManager.get_config()
    ext_agents = conf.get("external_agents", {})

    # Resolve external agent config key (e.g. "coding-agent") → actual Docker container name
    # Docker Compose adds project prefix + replica suffix, so use `docker ps --filter` to find it
    actual_service = service
    if service in ext_agents:
        cnames = ext_agents[service].get("container_names", [])
        if cnames:
            ps = subprocess.run(
                ["docker", "ps", "-a", "--filter", f"name={cnames[0]}", "--format", "{{.Names}}"],
                capture_output=True, text=True
            )
            matches = [n for n in ps.stdout.strip().splitlines() if cnames[0] in n]
            if matches:
                actual_service = matches[0]

    # Validate resolved name to prevent command injection
    allowed_prefixes = ("mateclaw", "mcp-", "bot-", "postgres", "gpt-vis")
    ext_containers = {c for a in ext_agents.values() for c in a.get("container_names", [])}
    if not any(actual_service.startswith(p) for p in allowed_prefixes) and actual_service not in ext_containers:
        raise HTTPException(status_code=400, detail="Invalid service name.")

    cmd = ["docker", "logs", "--tail", str(tail), actual_service]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return {"logs": res.stdout + res.stderr}

@server.get("/")
def read_index(): return FileResponse(os.path.join(PATHS["frontend"], "index.html"))

# --- CLI App ---
app = typer.Typer(help=f"Mateclaw Agent Ecosystem CLI v{VERSION}", rich_markup_mode="rich")
config_app = typer.Typer(help="Manage configuration."); app.add_typer(config_app, name="config")
db_app = typer.Typer(help="Manage database."); app.add_typer(db_app, name="database")
agent_app = typer.Typer(help="Manage external agents."); app.add_typer(agent_app, name="agent")

@app.command()
def onboard():
    """Run configuration wizard."""
    os.makedirs(os.path.dirname(PATHS["env"]), exist_ok=True)
    if not os.path.exists(PATHS["env"]):
        template_path = ".env.template"
        if os.path.exists(template_path):
            shutil.copy(template_path, PATHS["env"])
            console.print(f"[bold green]Created {PATHS['env']} from template.[/bold green]")
        else:
            # Create a blank file if template is not found
            with open(PATHS["env"], "w") as f:
                pass
            console.print(f"[yellow]Warning: {template_path} not found. Created a blank {PATHS['env']}.[/yellow]")

    console.print(Panel.fit("🤖 [bold blue]Mateclaw Onboarding[/bold blue]"))
    mode = questionary.select("Mode:", choices=["standard", "local"]).ask()
    default_db = "postgresql+asyncpg://mate:mate_pass@postgres:5432/mate_db" if mode == "standard" else "sqlite+aiosqlite:////app/data/mateclaw_agent.db"
    db_uri = questionary.text("DB URI:", default=default_db).ask()
    
    console.print(Panel.fit("🤖 [bold blue]Model Configuration[/bold blue]"))
    model_provider = questionary.select(
        "Select Model Provider:",
        choices=[
            questionary.Choice("Google Gemini", value="gemini"),
            questionary.Choice("LiteLLM (for OpenAI, Anthropic, Ollama, etc.)", value="litellm"),
        ]
    ).ask()

    if model_provider == "gemini":
        set_key(PATHS["env"], "MATECLAW_AGENT_MODEL_PROVIDER", "gemini")
        api_key = questionary.password("Google API Key:").ask()
        if api_key: set_key(PATHS["env"], "GOOGLE_API_KEY", api_key)
        model_name = questionary.text("Gemini Model Name:", default="gemini-2.5-flash").ask()
        if model_name: set_key(PATHS["env"], "MATECLAW_AGENT_GEMINI_MODEL", model_name)

    elif model_provider == "litellm":
        set_key(PATHS["env"], "MATECLAW_AGENT_MODEL_PROVIDER", "litellm")
        model_name = questionary.text("LiteLLM Model Name (e.g., ollama/llama3):", default="ollama/llama3").ask()
        if model_name: set_key(PATHS["env"], "LITELLM_MODEL", model_name)
        api_base = questionary.text("LiteLLM API Base URL (e.g., http://host.docker.internal:11434 for Ollama):").ask()
        if api_base: set_key(PATHS["env"], "LITELLM_API_BASE", api_base)
        api_key = questionary.password("LiteLLM API Key (optional):").ask()
        if api_key: set_key(PATHS["env"], "LITELLM_API_KEY", api_key)
        skip_special = questionary.confirm("Skip special tokens?", default=False).ask()
        set_key(PATHS["env"], "LITELLM_SKIP_SPECIAL_TOKENS", "True" if skip_special else "False")

    console.print(Panel.fit("🌏 [bold blue]System Configuration[/bold blue]"))
    common_timezones = [
        "UTC", "Asia/Taipei", "Asia/Tokyo", "Asia/Shanghai", "Asia/Singapore",
        "Asia/Seoul", "Asia/Hong_Kong", "America/New_York", "America/Los_Angeles",
        "America/Chicago", "Europe/London", "Europe/Paris", "Europe/Berlin",
        "Australia/Sydney", "Custom..."
    ]
    tz_choice = questionary.select("Timezone:", choices=common_timezones, default="UTC").ask()
    if tz_choice == "Custom...":
        tz_choice = questionary.text(
            "Enter timezone (pytz format, e.g. America/Toronto):", default="UTC"
        ).ask()
    if tz_choice:
        set_key(PATHS["env"], "TIMEZONE", tz_choice)

    platforms = questionary.checkbox(
        "Select Channels to enable:",
        choices=[
            questionary.Choice("Telegram", value="tg"),
            questionary.Choice("Discord", value="dc"),
            questionary.Choice("Line", value="line"),
            questionary.Choice("Email (SMTP)", value="email"),
        ]
    ).ask()
    
    set_key(PATHS["env"], "ADK_SESSION_SERVICE_URI", db_uri)
    
    if platforms:
        for p in platforms:
            if p == "tg":
                token = questionary.password("Telegram Bot Token:").ask()
                if token: set_key(PATHS["env"], "TELEGRAM_BOT_TOKEN", token)
            elif p == "dc":
                token = questionary.password("Discord Bot Token:").ask()
                if token: set_key(PATHS["env"], "DISCORD_BOT_TOKEN", token)
            elif p == "line":
                token = questionary.password("Line Channel Access Token:").ask()
                secret = questionary.password("Line Channel Secret:").ask()
                if token: set_key(PATHS["env"], "LINE_CHANNEL_ACCESS_TOKEN", token)
                if secret: set_key(PATHS["env"], "LINE_CHANNEL_SECRET", secret)
            elif p == "email":
                server_addr = questionary.text("SMTP Server (e.g. smtp.gmail.com):").ask()
                port = questionary.text("SMTP Port:", default="465").ask()
                user = questionary.text("SMTP User (Email):").ask()
                password = questionary.password("SMTP Password:").ask()
                if server_addr: set_key(PATHS["env"], "SMTP_SERVER", server_addr)
                if port: set_key(PATHS["env"], "SMTP_PORT", port)
                if user: set_key(PATHS["env"], "SMTP_USER", user)
                if password: set_key(PATHS["env"], "SMTP_PASSWORD", password)

    conf = ConfigManager.get_config()
    conf.update({
        "mode": mode,
        "channels": [p for p in (platforms or []) if p != "email"],
        "model_provider": model_provider,
    })
    conf.setdefault("external_agents", {})
    if "mcp" not in conf or not conf["mcp"]:
        conf["mcp"] = ["mateclaw"]
    ConfigManager.save_config(conf)
    ConfigManager.update_external_agents_env()

    console.print(Panel.fit("🤖 [bold blue]Docker Setup[/bold blue]"))
    base_compose_file = "docker-compose.local.yaml" if mode == "local" else "docker-compose.yaml"
    src_compose = os.path.join(_project_root, base_compose_file)
    mate_dir = os.path.join(_project_root, ".mateclaw")
    dest_compose = os.path.join(mate_dir, base_compose_file)
    
    with open(src_compose, "r") as f:
        compose_content = f.read()

    compose_content = compose_content.replace(
        "build: .", "build: .."
    ).replace(
        "context: .", "context: .."
    ).replace(
        "- ./src", "- ../src"
    ).replace(
        "- ./mcp_servers", "- ../mcp_servers"
    )

    with open(dest_compose, "w") as f:
        f.write(compose_content)

    console.print(f"[bold green]Generated {dest_compose}[/bold green]")
    
    if questionary.confirm("Do you want to build Docker images now?").ask():
        console.print("Building Docker images...")
        cmd = DockerManager.get_cmd() + ["-f", base_compose_file, "build"]
        subprocess.run(cmd, check=True, cwd=mate_dir)
        
    console.print("[bold green]Success! Run 'mateclaw start' to begin.[/bold green]")

@app.command()
def start(build: bool = typer.Option(True, "--build/--no-build")):
    """Start Mateclaw services."""
    conf = ConfigManager.get_config()
    services = ["mateclaw-agent", "postgres"]
    for p in conf.get("channels", []): services.append(f"bot-{'telegram' if p=='tg' else 'discord' if p=='dc' else 'line'}")
    for m in conf.get("mcp", []):
        services.append(f"mcp-{m}")

    compose_file = "docker-compose.local.yaml" if conf.get("mode") == "local" else "docker-compose.yaml"
    compose_cwd = DockerManager.get_compose_cwd(compose_file)
    cmd = DockerManager.get_cmd() + ["-f", compose_file]

    cmd += ["up", "-d"]
    if build: cmd.append("--build")
    cmd.extend(services)

    console.print(f"Starting Mateclaw (Mode: [bold blue]{conf.get('mode')}[/bold blue])...")
    subprocess.run(cmd, check=True, cwd=compose_cwd)
    console.print("[bold green]SUCCESS: Mateclaw started![/bold green]")

@app.command()
def stop():
    """Stop all services."""
    conf = ConfigManager.get_config()
    compose_file = "docker-compose.local.yaml" if conf.get("mode") == "local" else "docker-compose.yaml"
    compose_cwd = DockerManager.get_compose_cwd(compose_file)
    subprocess.run(DockerManager.get_cmd() + ["-f", compose_file, "down"], check=True, cwd=compose_cwd)
    # Kill any dashboard process holding port 8501
    try:
        result = subprocess.run(["lsof", "-ti", ":8501"], capture_output=True, text=True)
        pids = result.stdout.strip().split()
        for pid in pids:
            if pid:
                subprocess.run(["kill", pid], capture_output=True)
    except Exception:
        pass

@app.command()
def status():
    """Check services status."""
    conf = ConfigManager.get_config()
    compose_file = "docker-compose.local.yaml" if conf.get("mode") == "local" else "docker-compose.yaml"
    compose_cwd = DockerManager.get_compose_cwd(compose_file)
    subprocess.run(DockerManager.get_cmd() + ["-f", compose_file, "ps"], check=True, cwd=compose_cwd)

@app.command()
def logs(service: str = typer.Argument(None)):
    """Show services logs."""
    conf = ConfigManager.get_config()
    compose_file = "docker-compose.local.yaml" if conf.get("mode") == "local" else "docker-compose.yaml"
    compose_cwd = DockerManager.get_compose_cwd(compose_file)
    cmd = DockerManager.get_cmd() + ["-f", compose_file, "logs", "--tail", "100"]
    if service:
        cmd.append(service)
    subprocess.run(cmd, check=True, cwd=compose_cwd)

@app.command()
def dashboard(port: int = 8501):
    """Launch the Web Dashboard."""
    console.print(f"Launching Dashboard at http://localhost:{port}...")
    threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(f"http://localhost:{port}")), daemon=True).start()
    uvicorn.run(server, host="0.0.0.0", port=port, log_level="error")

@db_app.command()
def info():
    """Show DB info and statistics."""
    counts = DatabaseManager.get_table_counts()
    table = Table(title="Database Stats")
    table.add_column("Table", style="cyan"); table.add_column("Count", justify="right", style="green")
    for t, c in counts.items(): table.add_row(t, str(c))
    console.print(table)

@db_app.command()
def backup(output: str = typer.Argument(None)):
    """Create full DB backup."""
    output = output or f"mateclaw_backup_{int(time.time())}.json"
    engine = DatabaseManager.get_engine()
    tables = ["events", "sessions", "user_states", "reminders", "identity_maps", "file_tasks", "user_contacts", "contacts", "tasks"]
    data = {"version": VERSION, "timestamp": datetime.now().isoformat(), "tables": {}}
    with engine.connect() as conn:
        for t in tables:
            try:
                res = conn.execute(text(f"SELECT * FROM {t}"))
                rows = [dict(row._mapping) for row in res]
                for r in rows:
                    for k, v in r.items():
                        if isinstance(v, datetime): r[k] = v.isoformat()
                data["tables"][t] = rows
            except: pass
    with open(output, "w") as f: json.dump(data, f, indent=2)
    console.print(f"Backup saved to {output}")

@db_app.command()
def restore(file_path: str):
    """Restore from JSON backup."""
    if not os.path.exists(file_path): return console.print("File not found.")
    with open(file_path, "r") as f: data = json.load(f)
    if not questionary.confirm("Overwrite existing data?").ask(): return
    engine = DatabaseManager.get_engine()
    with engine.connect() as conn:
        for t, rows in data.get("tables", {}).items():
            if not rows: continue
            conn.execute(text(f"DELETE FROM {t}"))
            cols = rows[0].keys()
            conn.execute(text(f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join([':'+c for c in cols])})"), rows)
        conn.commit()
    console.print("Restore complete.")

@db_app.command()
def clean():
    """Wipe database data."""
    if questionary.confirm("Wipe all data?").ask():
        engine = DatabaseManager.get_engine()
        tables = ["events", "sessions", "user_states", "reminders", "identity_maps", "file_tasks", "user_contacts", "contacts", "tasks"]
        is_sqlite = "sqlite" in str(engine.url)
        with engine.connect() as conn:
            for t in tables:
                try:
                    conn.execute(text(f"DELETE FROM {t}"))
                    if is_sqlite:
                        conn.execute(text("DELETE FROM sqlite_sequence WHERE name=:t"), {"t": t})
                except: pass
            conn.commit()
        console.print("Database wiped.")

def _next_available_port(conf: dict) -> int:
    used = {a.get("public_port") for a in conf.get("external_agents", {}).values() if a.get("public_port")}
    for p in range(18100, 18200):
        if p not in used:
            return p
    raise RuntimeError("No available ports in range 18100-18199")

def _deploy_local_agent(name: str, source_path: str, conf: dict) -> dict:
    """Build and start a local-path agent following Mateclaw Agent Convention."""
    import yaml as _yaml
    source_path = os.path.abspath(source_path)
    manifest_path = os.path.join(source_path, "mateclaw.agent.json")
    compose_path = os.path.join(source_path, "docker-compose.yaml")

    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"mateclaw.agent.json not found in {source_path}")
    if not os.path.exists(compose_path):
        raise FileNotFoundError(f"docker-compose.yaml not found in {source_path}")

    with open(manifest_path) as f:
        manifest = json.load(f)

    a2a_service = manifest.get("a2a_service", name)
    port = manifest.get("port", 8081)
    description = manifest.get("description", "")
    version = manifest.get("version", "")

    # Check env_required
    load_dotenv(PATHS["env"])
    missing = [k for k in manifest.get("env_required", []) if not os.getenv(k)]
    if missing:
        for k in missing:
            val = questionary.password(f"Required env var {k}:").ask()
            if not val:
                raise ValueError(f"Required env var {k} not provided")
            set_key(PATHS["env"], k, val)
        load_dotenv(PATHS["env"], override=True)

    public_port = _next_available_port(conf)
    fragment_dir = os.path.join(_project_root, ".mateclaw", "external-agents", name)
    os.makedirs(fragment_dir, exist_ok=True)

    # Read source compose to get all service names
    with open(compose_path) as f:
        src_compose = _yaml.safe_load(f)
    service_names = list(src_compose.get("services", {}).keys())

    # Build env injection from env_required + env_optional
    env_vars = []
    for k in manifest.get("env_required", []) + manifest.get("env_optional", []):
        v = os.getenv(k, "")
        env_vars.append(f"      - {k}={v}")

    # Generate compose fragment
    services_fragment = {}
    for svc in service_names:
        ext_svc = f"mateclaw-ext-{name}-{svc}" if svc != a2a_service else f"mateclaw-ext-{name}"
        svc_def = src_compose["services"][svc].copy()
        # Rewrite build context to absolute path
        if "build" in svc_def:
            build = svc_def["build"]
            if isinstance(build, str):
                svc_def["build"] = os.path.join(source_path, build)
            elif isinstance(build, dict) and "context" in build:
                svc_def["build"]["context"] = os.path.join(source_path, build["context"])
        # Remove original ports (we manage them)
        svc_def.pop("ports", None)
        # Add to mateclaw network
        svc_def.setdefault("networks", [])
        if isinstance(svc_def["networks"], list) and "mateclaw_default" not in svc_def["networks"]:
            svc_def["networks"].append("mateclaw_default")
        # Inject env vars into a2a service
        if svc == a2a_service:
            svc_def.setdefault("environment", [])
            svc_def["environment"] += [f"PORT={port}", f"PUBLIC_HOST=mateclaw-ext-{name}"]
            for k in manifest.get("env_required", []) + manifest.get("env_optional", []):
                v = os.getenv(k, "")
                if v: svc_def["environment"].append(f"{k}={v}")
            svc_def["ports"] = [f"127.0.0.1:{public_port}:{port}"]
        # Rename depends_on references
        if "depends_on" in svc_def:
            old_deps = svc_def["depends_on"]
            if isinstance(old_deps, list):
                svc_def["depends_on"] = [
                    f"mateclaw-ext-{name}-{d}" if d != a2a_service else f"mateclaw-ext-{name}"
                    for d in old_deps
                ]
        services_fragment[ext_svc] = svc_def

    # Volumes: use mateclaw_data (external) for data dirs; declare others as local
    volumes_fragment = {}
    for svc_def in services_fragment.values():
        for vol in svc_def.get("volumes", []):
            vol_name = vol.split(":")[0] if ":" in str(vol) else None
            if vol_name and not vol_name.startswith("/") and vol_name not in volumes_fragment:
                volumes_fragment[vol_name] = None  # local volume

    fragment = {
        "services": services_fragment,
        "networks": {"mateclaw_default": {"external": True}},
    }
    if volumes_fragment:
        fragment["volumes"] = volumes_fragment
    fragment_path = os.path.join(fragment_dir, "compose-fragment.yaml")
    with open(fragment_path, "w") as f:
        _yaml.dump(fragment, f, default_flow_style=False, allow_unicode=True)

    # Build & start
    main_compose = os.path.join(_project_root, ".mateclaw", "docker-compose.yaml")
    ext_services = list(services_fragment.keys())
    cmd = DockerManager.get_cmd() + ["-f", main_compose, "-f", fragment_path, "up", "-d", "--build"] + ext_services
    console.print(f"Building and starting {name}...")
    result = subprocess.run(cmd, cwd=_project_root)
    if result.returncode != 0:
        raise RuntimeError("docker compose up failed")

    # Health check (30s)
    health_url = f"http://localhost:{public_port}/.well-known/agent.json"
    console.print(f"Waiting for health check at {health_url}...")
    for _ in range(10):
        time.sleep(3)
        try:
            r = httpx.get(health_url, timeout=3.0)
            if r.status_code == 200:
                console.print("[green]Agent is healthy![/green]")
                break
        except: pass
    else:
        console.print("[yellow]Warning: health check timed out. Agent may still be starting.[/yellow]")

    result = {
        "type": "github",
        "source_path": source_path,
        "fragment_path": fragment_path,
        "a2a_url": f"http://mateclaw-ext-{name}:{port}",
        "public_port": public_port,
        "description": description,
        "version": version,
        "enabled": True,
        "container_names": ext_services,
    }
    # Read mcp_configurable capability from manifest
    if manifest.get("mcp_configurable"):
        result["mcp_configurable"] = True
        result["mcp_env_var"] = manifest.get("mcp_env_var", name.replace("-", "_").upper() + "_MCP_URLS")
    return result

@agent_app.command("add")
def agent_add(
    name: str = typer.Argument(..., help="Agent name (e.g. market-analyst)"),
    url: Optional[str] = typer.Option(None, "--url", help="Remote A2A endpoint URL"),
    local: Optional[str] = typer.Option(None, "--local", help="Local project path (Mateclaw Agent Convention)"),
    description: str = typer.Option("", "--description", "-d", help="Short description"),
):
    """Add an external agent (URL mode or local deploy)."""
    if not url and not local:
        console.print("[red]Error: --url or --local is required[/red]"); raise typer.Exit(1)
    name = name.strip().lower().replace(" ", "-")
    if not re.match(r'^[a-z0-9][a-z0-9_-]*$', name):
        console.print("[red]Error: name must be lowercase alphanumeric with hyphens/underscores[/red]"); raise typer.Exit(1)
    conf = ConfigManager.get_config()
    if name in conf.get("external_agents", {}):
        console.print(f"[red]Error: Agent '{name}' already exists. Use 'mateclaw agent remove {name}' first.[/red]"); raise typer.Exit(1)

    if local:
        try:
            entry = _deploy_local_agent(name, local, conf)
        except Exception as e:
            console.print(f"[red]Deploy failed: {e}[/red]"); raise typer.Exit(1)
    else:
        entry = {"type": "url", "a2a_url": url, "description": description, "enabled": True}

    conf.setdefault("external_agents", {})[name] = entry
    ConfigManager.save_config(conf)
    ConfigManager.update_external_agents_env()
    threading.Thread(target=DockerManager.run_action, args=("mateclaw-agent", "restart"), daemon=True).start()
    console.print(f"[green]Agent '{name}' deployed and registered.[/green]")

@agent_app.command("list")
def agent_list():
    """List all external agents with health status."""
    conf = ConfigManager.get_config()
    agents = conf.get("external_agents", {})
    if not agents:
        console.print("[yellow]No external agents configured.[/yellow]"); return
    table = Table(title="External Agents")
    table.add_column("Name", style="cyan"); table.add_column("Type", style="blue")
    table.add_column("A2A URL"); table.add_column("Health", justify="center")
    table.add_column("Enabled", justify="center"); table.add_column("Description")
    for name, agent in agents.items():
        health = "—"
        if agent.get("a2a_url") and agent.get("enabled"):
            try:
                r = httpx.get(f"{agent['a2a_url']}/.well-known/agent.json", timeout=3.0)
                health = "[green]●[/green]" if r.status_code == 200 else "[red]●[/red]"
            except: health = "[red]●[/red]"
        table.add_row(name, agent.get("type","url"), agent.get("a2a_url",""), health,
                      "✓" if agent.get("enabled") else "✗", (agent.get("description","") or "")[:50])
    console.print(table)

@agent_app.command("remove")
def agent_remove(name: str = typer.Argument(..., help="Agent name to remove")):
    """Remove an external agent."""
    conf = ConfigManager.get_config()
    if name not in conf.get("external_agents", {}):
        console.print(f"[red]Error: Agent '{name}' not found.[/red]"); raise typer.Exit(1)
    if not questionary.confirm(f"Remove agent '{name}'?").ask(): return
    del conf["external_agents"][name]
    if name == "coding-agent": conf["coding_agent_enabled"] = False
    ConfigManager.save_config(conf)
    ConfigManager.update_external_agents_env()
    console.print(f"[green]Agent '{name}' removed.[/green]")
    console.print("[yellow]Restart mateclaw-agent to apply: mateclaw start[/yellow]")

@agent_app.command("enable")
def agent_enable(name: str = typer.Argument(...)):
    """Enable an external agent."""
    conf = ConfigManager.get_config()
    if name not in conf.get("external_agents", {}):
        console.print(f"[red]Error: Agent '{name}' not found.[/red]"); raise typer.Exit(1)
    conf["external_agents"][name]["enabled"] = True
    if name == "coding-agent": conf["coding_agent_enabled"] = True
    ConfigManager.save_config(conf)
    ConfigManager.update_external_agents_env()
    console.print(f"[green]Agent '{name}' enabled.[/green]")

@agent_app.command("disable")
def agent_disable(name: str = typer.Argument(...)):
    """Disable an external agent."""
    conf = ConfigManager.get_config()
    if name not in conf.get("external_agents", {}):
        console.print(f"[red]Error: Agent '{name}' not found.[/red]"); raise typer.Exit(1)
    conf["external_agents"][name]["enabled"] = False
    if name == "coding-agent": conf["coding_agent_enabled"] = False
    ConfigManager.save_config(conf)
    ConfigManager.update_external_agents_env()
    console.print(f"[green]Agent '{name}' disabled.[/green]")

@app.command()
def chat(app_name: str = typer.Option(None)):
    """Interactive CLI Chat."""
    try:
        apps = httpx.get("http://localhost:18080/list-apps").json()
    except: return console.print("Mateclaw Agent not running.")
    app_name = app_name or questionary.select("Select App:", choices=apps).ask()
    sid = f"cli-{secrets.token_hex(4)}"
    console.print(f"Chatting with {app_name} (Session: {sid})")
    try:
        httpx.post(f"http://localhost:18080/apps/{app_name}/users/cli-user/sessions/{sid}", json={"state": {}})
    except: pass
    while True:
        q = questionary.text("You:").ask()
        if not q or q.lower() in ["exit", "quit"]: break
        try:
            with httpx.stream("POST", "http://localhost:18080/run_sse", json={"app_name": app_name, "user_id": "cli-user", "session_id": sid, "new_message": {"role": "user", "parts": [{"text": q}]}, "streaming": True}, timeout=None) as r:
                console.print("Agent: ", end="")
                for line in r.iter_lines():
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            for p in data.get("content", {}).get("parts", []):
                                if t := p.get("text"): console.print(t, end="")
                        except: pass
                console.print("\n")
        except: break

@app.command()
def license(
    action: str = typer.Argument(..., help="apply | status"),
    path: str = typer.Argument(None, help="Path to mateclaw-license.yaml (for apply)"),
):
    """Manage your Mateclaw Agent license."""
    import shutil
    import sys
    sys.path.insert(0, _project_root)
    from src.core.license import LicenseManager, OSS_LIMITS

    mate_dir = os.path.join(_project_root, ".mateclaw")
    dest = os.path.join(mate_dir, "mateclaw-license.yaml")

    if action == "apply":
        if not path:
            console.print("[red]Please provide the path to your license file.[/red]")
            console.print("  Usage: mateclaw license apply ./mateclaw-license.yaml")
            raise typer.Exit(1)
        if not os.path.exists(path):
            console.print(f"[red]File not found: {path}[/red]")
            raise typer.Exit(1)
        try:
            info = LicenseManager.load(path)
            if info:
                shutil.copy(path, dest)
                console.print(Panel.fit(
                    f"[green]✔ License applied successfully[/green]\n\n"
                    f"  Plan       : [bold]{info.plan.upper()}[/bold]\n"
                    f"  Issued to  : {info.issued_to}\n"
                    f"  Expires    : {info.expires_at or 'Never'}\n"
                    f"  extra_mcp  : {info.extra_mcp}\n"
                    f"  backlog    : {info.backlog_tasks}\n"
                    f"  reminders  : {info.reminders}",
                    title="Mateclaw License"
                ))
        except ValueError as e:
            console.print(f"[red]✖ License rejected: {e}[/red]")
            raise typer.Exit(1)

    elif action == "machine-id":
        from src.core.license import get_machine_id
        mid = get_machine_id()
        console.print(Panel.fit(
            f"  Machine ID : [bold]{mid}[/bold]\n\n"
            f"  Provide this to the licensor when purchasing an Enterprise License.",
            title="Mateclaw Machine ID"
        ))

    elif action == "status":
        if os.path.exists(dest):
            try:
                info = LicenseManager.load(dest)
                expired_note = " [red](EXPIRED)[/red]" if info and info.is_expired else ""
                console.print(Panel.fit(
                    f"  Plan       : [bold]{info.plan.upper()}[/bold]{expired_note}\n"
                    f"  Issued to  : {info.issued_to}\n"
                    f"  Expires    : {info.expires_at or 'Never'}\n"
                    f"  extra_mcp  : {info.extra_mcp}\n"
                    f"  backlog    : {info.backlog_tasks}\n"
                    f"  reminders  : {info.reminders}",
                    title="Mateclaw License"
                ))
            except ValueError as e:
                console.print(f"[red]License invalid: {e}[/red]")
        else:
            console.print(Panel.fit(
                f"  Plan       : [bold]OSS[/bold]\n"
                f"  extra_mcp  : {OSS_LIMITS['extra_mcp']}\n"
                f"  backlog    : {OSS_LIMITS['backlog_tasks']}\n"
                f"  reminders  : {OSS_LIMITS['reminders']}\n\n"
                f"  To upgrade, contact: simonliuyuwei@gmail.com",
                title="Mateclaw License"
            ))
    else:
        console.print(f"[red]Unknown action: {action}. Use 'apply' or 'status'.[/red]")
        raise typer.Exit(1)


if __name__ == "__main__": app()
