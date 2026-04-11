import json
import secrets
import threading
import time
import webbrowser

import httpx
import questionary
import typer
import uvicorn
from rich.console import Console

from server.app import server

console = Console()


def dashboard(port: int = 8501):
    """Launch the Web Dashboard."""
    console.print(f"Launching Dashboard at http://localhost:{port}...")
    threading.Thread(target=lambda: (time.sleep(1.5), webbrowser.open(f"http://localhost:{port}")), daemon=True).start()
    uvicorn.run(server, host="0.0.0.0", port=port, log_level="error")


def chat(app_name: str = typer.Option(None)):
    """Interactive CLI Chat."""
    try:
        apps = httpx.get("http://localhost:18080/list-apps").json()
    except Exception:
        return console.print("CoStaff Agent not running.")
    app_name = app_name or questionary.select("Select App:", choices=apps).ask()
    sid = f"cli-{secrets.token_hex(4)}"
    console.print(f"Chatting with {app_name} (Session: {sid})")
    try:
        httpx.post(f"http://localhost:18080/apps/{app_name}/users/cli-user/sessions/{sid}", json={"state": {}})
    except Exception:
        pass
    while True:
        q = questionary.text("You:").ask()
        if not q or q.lower() in ["exit", "quit"]:
            break
        try:
            with httpx.stream("POST", "http://localhost:18080/run_sse", json={"app_name": app_name, "user_id": "cli-user", "session_id": sid, "new_message": {"role": "user", "parts": [{"text": q}]}, "streaming": True}, timeout=None) as r:
                console.print("Agent: ", end="")
                for line in r.iter_lines():
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            for p in data.get("content", {}).get("parts", []):
                                if t := p.get("text"):
                                    console.print(t, end="")
                        except Exception:
                            pass
                console.print("\n")
        except Exception:
            break
