import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from utils.paths import PATHS


class DatabaseManager:
    @staticmethod
    def get_engine():
        # Resolve via the active CoStaff core (single-core install falls back to
        # the host .env's ADK_SESSION_SERVICE_URI — identical to the old behaviour).
        from services.cores import active_core
        load_dotenv(PATHS["env"])
        return active_core().engine()

    @staticmethod
    def get_table_counts():
        engine = DatabaseManager.get_engine()
        if not engine:
            return {}
        counts = {}
        tables = ["events", "sessions", "user_states", "reminders", "regular_works", "epics", "project_tasks", "diary", "identity_maps", "file_tasks", "user_contacts", "contacts"]
        with engine.connect() as conn:
            for t in tables:
                try:
                    counts[t] = conn.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
                except Exception:
                    counts[t] = "N/A"
        return counts
