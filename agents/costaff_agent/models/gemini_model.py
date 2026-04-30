import os

# Gemini model name to send to ADK. ADK accepts a plain string for Gemini.
# Override via COSTAFF_AGENT_GEMINI_MODEL env var.
gemini_model = os.getenv("COSTAFF_AGENT_GEMINI_MODEL", "gemini-2.5-flash")
