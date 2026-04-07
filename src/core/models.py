import uuid
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base
import enum

Base = declarative_base()

def generate_uuid():
    """Helper to generate standard UUID strings for primary keys."""
    return str(uuid.uuid4())

class Reminder(Base):
    """
    Core model for scheduled tasks.
    Stores content, timing (run_at or cron), and delivery channel info.
    """
    __tablename__ = "reminders"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    prompt = Column(String, nullable=True) # The user's original request
    run_at = Column(DateTime, nullable=True) # Scheduled execution time (optional if cron is used)
    channel = Column(String, nullable=False)  # target: telegram, line, email
    recipient = Column(String, nullable=False) # Physical ID or email address
    status = Column(String, default="pending") # Cycle: pending -> completed/failed
    cron = Column(String, nullable=True) # Optional pattern for recurring jobs
    subject = Column(String, nullable=True) # Subject line (mainly for email)
    body = Column(String, nullable=True) # The actual resolved message text to send
    
    # Context required to report execution results back to the ADK agent
    app_name = Column(String, nullable=False)
    user_id = Column(String, nullable=False)
    session_id = Column(String, nullable=False)

class UserContact(Base):
    """
    Stores persistent metadata about the user.
    This enables the AI Agent to have 'memory' of who it's assisting.
    """
    __tablename__ = "user_contacts"

    user_id = Column(String, primary_key=True)
    chinese_name = Column(String, nullable=True)
    english_name = Column(String, nullable=True)
    job_title = Column(String, nullable=True)
    company_name = Column(String, nullable=True)
    company_address = Column(String, nullable=True)
    tax_id = Column(String, nullable=True)
    personal_email = Column(String, nullable=True)
    company_phone = Column(String, nullable=True)
    mobile_phone = Column(String, nullable=True)
    employee_id = Column(String, nullable=True)
    note = Column(String, nullable=True)

class Contact(Base):
    """
    Represents entries in the user's personal address book.
    Used for sending messages to others (e.g., 'remind my manager to...').
    """
    __tablename__ = "contacts"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    owner_id = Column(String, nullable=False) # Links the contact to a specific user
    name = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    company = Column(String, nullable=True)
    note = Column(String, nullable=True)

class IdentityMap(Base):
    """
    Security mapping between Session IDs and real platform IDs (e.g., Discord Channel ID).
    Allows the system to route proactive notifications back to the specific context
    where the conversation originated.
    """
    __tablename__ = "identity_maps"

    session_id = Column(String, primary_key=True) # e.g., dc_channelID_userHash
    hashed_id = Column(String, nullable=False, index=True) # The 16-char user Context ID
    real_id = Column(String, nullable=False) # The actual platform ID (Channel ID or Chat ID)
    is_approved = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class FileTask(Base):
    """
    Tracks asynchronous file processing jobs (e.g., PDF parsing on PrivAI).
    Used by the background poller to know when to trigger 'completed' notifications.
    """
    __tablename__ = "file_tasks"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    file_id = Column(String(36), nullable=False, index=True) # The UUID from PrivAI cloud
    user_id = Column(String, nullable=False)
    session_id = Column(String, nullable=False)
    app_name = Column(String, default="mate-agent")
    filename = Column(String, nullable=True)
    status = Column(String, default="parsing") # Cycle: parsing -> completed/failed
    created_at = Column(DateTime, default=datetime.utcnow)

class Task(Base):
    """
    Model for Agent Task Dashboard.
    Supports Kanban status, Spec, Cron scheduling, and Result reporting.
    """
    __tablename__ = "tasks"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    title = Column(String, nullable=False)
    spec = Column(String, nullable=False)  # User requirements
    status = Column(String, default="backlog")  # backlog, doing, done, failed
    cron = Column(String, nullable=True)  # Cron pattern for scheduling
    channel = Column(String, nullable=True)  # discord, line, telegram, etc.
    recipient = Column(String, nullable=True) # Channel ID or User ID
    result = Column(String, nullable=True)  # Final outcome from agent
    last_run = Column(DateTime, nullable=True)
    next_run = Column(DateTime, nullable=True)
    user_id = Column(String, nullable=False)
    session_id = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class TaskLog(Base):
    """
    Stores historical execution results for a specific Task.
    Allows users to see a history of what the agent did.
    user_id is denormalized here for efficient monthly execution counting.
    """
    __tablename__ = "task_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    task_id = Column(String(36), nullable=False, index=True)
    user_id = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False)
    output = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class ApiConfig(Base):
    """
    User-defined external API configurations callable by the Agent via MCP tools.
    Headers are Fernet-encrypted so secrets never appear in plaintext in the DB.
    """
    __tablename__ = "api_configs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String, nullable=False)
    url = Column(String, nullable=False)
    method = Column(String, nullable=False, default="GET")
    headers_encrypted = Column(String, nullable=True)  # Fernet-encrypted JSON string
    description = Column(String, nullable=True)         # Usage instructions for the Agent
    user_id = Column(String, nullable=False, index=True)
    agent_ids = Column(String, nullable=True, default="__all__")  # Comma-separated agent IDs or __all__
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SkillConfig(Base):
    """
    User-defined Skills: reusable capabilities the Agent can discover and invoke.
    Each Skill has a description + usage instructions (Markdown) the Agent reads to
    understand how to fulfil a request. Optionally backed by a remote AI endpoint.
    """
    __tablename__ = "skill_configs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)          # Brief summary for listing/search
    tags = Column(String, nullable=True)                 # Comma-separated tags for search
    usage = Column(String, nullable=True)                # Markdown: detailed instructions for the Agent
    user_id = Column(String, nullable=False, index=True)
    agent_ids = Column(String, nullable=True, default="__all__")  # Comma-separated agent IDs or __all__
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)