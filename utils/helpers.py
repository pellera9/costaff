"""Backward-compatibility shim — every name once defined here now lives in
a domain-focused module. New code should import directly from those modules:

    utils.paths          — paths/constants (VERSION, PATHS, _project_root, ...)
    utils.serialization  — datetime / row serialization
    utils.validators     — cron + a2a URL safety
    utils.ports          — dynamic port allocation
    utils.plugin_env     — interactive plugin .env management
    utils.compose        — channel compose-fragment generation
    utils.deploy         — local agent / channel deployers

This shim exists only because ~13 callers across cli/ and server/ still
import via `from utils.helpers import …`. Migrate callers in their own
commits, then delete this file.
"""
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
from .compose import _write_channel_fragment
from .deploy import _deploy_local_channel, _deploy_local_agent
