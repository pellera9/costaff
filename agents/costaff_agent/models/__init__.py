"""Model selector: pick the LLM to use based on COSTAFF_AGENT_MODEL_PROVIDER.

Usage:
    from .models import selected_model

Resolution rules:
    - COSTAFF_AGENT_MODEL_PROVIDER=litellm  → use the LiteLlm instance
                                              configured in litellm_model.py
    - anything else (default: 'gemini')     → use the Gemini model name
                                              from gemini_model.py

Add a new provider by:
    1. Creating <provider>_model.py with a top-level model object/string
    2. Importing it here and adding a branch below
"""
import os

from .gemini_model import gemini_model
from .litellm_model import litellm_model

_provider = (os.getenv("COSTAFF_AGENT_MODEL_PROVIDER") or "gemini").lower()

if _provider == "litellm":
    selected_model = litellm_model
else:
    selected_model = gemini_model
