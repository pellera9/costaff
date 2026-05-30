import os
from dotenv import load_dotenv
from google.adk.models.lite_llm import LiteLlm

# Load environment variables
load_dotenv()

# Read configuration from environment
MODEL_NAME                   = os.getenv("LITELLM_MODEL_NAME")
API_BASE                     = os.getenv("LITELLM_API_BASE")
API_KEY                      = os.getenv("LITELLM_API_KEY")
BOOL_SKIP_SPECIAL_TOKENS     = os.getenv("LITELLM_SKIP_SPECIAL_TOKENS", "False").lower() == "true"

# Initialize LiteLlm model — but ONLY when a model name is configured.
# models/__init__.py imports this module unconditionally, yet only USES
# litellm_model when COSTAFF_AGENT_MODEL_PROVIDER=litellm. Under the default
# gemini provider, LITELLM_MODEL_NAME is unset, so eagerly building
# LiteLlm(model=None) raises a pydantic ValidationError at import time, which
# crashes the whole manager agent module load and makes `adk web` return HTTP
# 500 on /run (with no traceback in the normal container logs). Guard it.
litellm_model = (
    LiteLlm(
        model=MODEL_NAME,
        api_base=API_BASE,
        api_key=API_KEY,
        extra_body={"skip_special_tokens": BOOL_SKIP_SPECIAL_TOKENS},
    )
    if MODEL_NAME
    else None
)
