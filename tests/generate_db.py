import os
import sys
from sqlalchemy import (
    create_engine,
    MetaData,
    Table,
    Column,
    Integer,
    String,
    Float,
    DateTime,
    Boolean,
    ForeignKey,
    Text,
    func,
)
from dotenv import load_dotenv

# Load environment variables (mostly for POSTGRES_URL)
load_dotenv()

def get_engine():
    """Get the PostgreSQL database engine."""
    db_url = os.getenv("POSTGRES_URL")
    if not db_url:
        print("Error: POSTGRES_URL environment variable not set.", file=sys.stderr)
        sys.exit(1)
        
    print(f"Connecting to Postgres database at: {db_url}")
    return create_engine(db_url, echo=False)

def create_large_schema(engine):
    """Create all 100 tables for the performance test schema."""
    metadata = MetaData()
    print("Defining 100-table schema...")

    # =====================================================================
    # Cluster A — Identity & Auth (12 tables)
    # =====================================================================
    users = Table(
        "users", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("email", String(255), nullable=False, unique=True),
        Column("name", String(255), nullable=False),
        Column("password_hash", String(255), nullable=False),
        Column("status", String(50), nullable=False, default="active"),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
        Column("deleted_at", DateTime, nullable=True),
    )

    user_profiles = Table(
        "user_profiles", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        Column("bio", Text, nullable=True),
        Column("avatar_url", String(255), nullable=True),
        Column("timezone", String(100), nullable=False, default="UTC"),
        Column("locale", String(50), nullable=False, default="en-US"),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    user_settings = Table(
        "user_settings", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        Column("theme", String(50), nullable=False, default="system"),
        Column("marketing_emails", Boolean, nullable=False, default=False),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    sessions = Table(
        "sessions", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("token", String(255), nullable=False, unique=True),
        Column("ip_address", String(45), nullable=True),
        Column("user_agent", Text, nullable=True),
        Column("expires_at", DateTime, nullable=False),
        Column("created_at", DateTime, server_default=func.now()),
    )

    oauth_providers = Table(
        "oauth_providers", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("name", String(100), nullable=False, unique=True),
        Column("client_id", String(255), nullable=False),
        Column("created_at", DateTime, server_default=func.now()),
    )

    oauth_connections = Table(
        "oauth_connections", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("provider_id", Integer, ForeignKey("oauth_providers.id", ondelete="CASCADE"), nullable=False),
        Column("provider_user_id", String(255), nullable=False),
        Column("created_at", DateTime, server_default=func.now()),
    )

    password_resets = Table(
        "password_resets", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("token", String(255), nullable=False, unique=True),
        Column("expires_at", DateTime, nullable=False),
        Column("used_at", DateTime, nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
    )

    two_factor_auth = Table(
        "two_factor_auth", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        Column("secret", String(255), nullable=False),
        Column("is_enabled", Boolean, nullable=False, default=False),
        Column("backup_codes", Text, nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    audit_logs = Table(
        "audit_logs", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("action", String(255), nullable=False),
        Column("entity_type", String(100), nullable=False),
        Column("entity_id", Integer, nullable=True),
        Column("details", Text, nullable=True),
        Column("ip_address", String(45), nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
    )

    roles = Table(
        "roles", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("name", String(100), nullable=False, unique=True),
        Column("description", Text, nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
    )

    permissions = Table(
        "permissions", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("resource", String(100), nullable=False),
        Column("action", String(100), nullable=False),
        Column("description", Text, nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
    )

    role_permissions = Table(
        "role_permissions", metadata,
        Column("role_id", Integer, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
        Column("permission_id", Integer, ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
        Column("created_at", DateTime, server_default=func.now()),
    )

    # =====================================================================
    # Cluster B — Organizations & Teams (10 tables)
    # =====================================================================
    organizations = Table(
        "organizations", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("name", String(255), nullable=False),
        Column("slug", String(255), nullable=False, unique=True),
        Column("billing_email", String(255), nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
        Column("deleted_at", DateTime, nullable=True),
    )

    org_members = Table(
        "org_members", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("role_id", Integer, ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False),
        Column("joined_at", DateTime, server_default=func.now()),
        Column("deleted_at", DateTime, nullable=True),
    )

    org_invitations = Table(
        "org_invitations", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("inviter_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("email", String(255), nullable=False),
        Column("role_id", Integer, ForeignKey("roles.id", ondelete="RESTRICT"), nullable=False),
        Column("token", String(255), nullable=False, unique=True),
        Column("expires_at", DateTime, nullable=False),
        Column("created_at", DateTime, server_default=func.now()),
    )

    teams = Table(
        "teams", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("name", String(255), nullable=False),
        Column("description", Text, nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
        Column("deleted_at", DateTime, nullable=True),
    )

    team_members = Table(
        "team_members", metadata,
        Column("team_id", Integer, ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        Column("joined_at", DateTime, server_default=func.now()),
    )

    org_settings = Table(
        "org_settings", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, unique=True),
        Column("allow_public_projects", Boolean, nullable=False, default=False),
        Column("sso_enabled", Boolean, nullable=False, default=False),
        Column("sso_domain", String(255), nullable=True),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    org_audit_logs = Table(
        "org_audit_logs", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("actor_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("action", String(255), nullable=False),
        Column("target_type", String(100), nullable=False),
        Column("target_id", Integer, nullable=True),
        Column("details", Text, nullable=True),
        Column("ip_address", String(45), nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
    )

    departments = Table(
        "departments", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("name", String(255), nullable=False),
        Column("head_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    department_members = Table(
        "department_members", metadata,
        Column("department_id", Integer, ForeignKey("departments.id", ondelete="CASCADE"), primary_key=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        Column("joined_at", DateTime, server_default=func.now()),
    )

    org_plans = Table(
        "org_plans", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, unique=True),
        Column("plan_tier", String(50), nullable=False, default="free"),
        Column("max_users", Integer, nullable=False, default=5),
        Column("max_projects", Integer, nullable=False, default=3),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    # =====================================================================
    # Cluster C — Projects & Tasks (14 tables)
    # =====================================================================
    projects = Table(
        "projects", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("name", String(255), nullable=False),
        Column("key", String(10), nullable=False),
        Column("description", Text, nullable=True),
        Column("status", String(50), nullable=False, default="active"),
        Column("owner_id", Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
        Column("deleted_at", DateTime, nullable=True),
    )

    project_members = Table(
        "project_members", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("project_id", Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("role", String(50), nullable=False, default="member"),
        Column("joined_at", DateTime, server_default=func.now()),
    )

    project_settings = Table(
        "project_settings", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("project_id", Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True),
        Column("is_public", Boolean, nullable=False, default=False),
        Column("default_assignee_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    milestones = Table(
        "milestones", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("project_id", Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        Column("name", String(255), nullable=False),
        Column("description", Text, nullable=True),
        Column("due_date", DateTime, nullable=True),
        Column("status", String(50), nullable=False, default="open"),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    sprints = Table(
        "sprints", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("project_id", Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        Column("name", String(255), nullable=False),
        Column("goal", Text, nullable=True),
        Column("start_date", DateTime, nullable=True),
        Column("end_date", DateTime, nullable=True),
        Column("status", String(50), nullable=False, default="planned"),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    tasks = Table(
        "tasks", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("project_id", Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        Column("key", String(20), nullable=False),
        Column("title", String(255), nullable=False),
        Column("description", Text, nullable=True),
        Column("status", String(50), nullable=False, default="todo"),
        Column("priority", String(50), nullable=False, default="medium"),
        Column("type", String(50), nullable=False, default="task"),
        Column("creator_id", Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        Column("assignee_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("sprint_id", Integer, ForeignKey("sprints.id", ondelete="SET NULL"), nullable=True),
        Column("milestone_id", Integer, ForeignKey("milestones.id", ondelete="SET NULL"), nullable=True),
        Column("parent_task_id", Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True),
        Column("story_points", Float, nullable=True),
        Column("due_date", DateTime, nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
        Column("deleted_at", DateTime, nullable=True),
    )

    task_assignments = Table(
        "task_assignments", metadata,
        Column("task_id", Integer, ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        Column("assigned_at", DateTime, server_default=func.now()),
        Column("assigned_by", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )

    task_labels = Table(
        "task_labels", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("project_id", Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        Column("name", String(50), nullable=False),
        Column("color", String(7), nullable=False, default="#808080"),
        Column("created_at", DateTime, server_default=func.now()),
    )

    task_label_map = Table(
        "task_label_map", metadata,
        Column("task_id", Integer, ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
        Column("label_id", Integer, ForeignKey("task_labels.id", ondelete="CASCADE"), primary_key=True),
        Column("added_at", DateTime, server_default=func.now()),
    )

    task_comments = Table(
        "task_comments", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("task_id", Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        Column("author_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("content", Text, nullable=False),
        Column("is_internal", Boolean, nullable=False, default=False),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
        Column("deleted_at", DateTime, nullable=True),
    )

    task_attachments = Table(
        "task_attachments", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("task_id", Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        Column("uploader_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("filename", String(255), nullable=False),
        Column("file_size", Integer, nullable=False),
        Column("content_type", String(100), nullable=False),
        Column("url", String(1024), nullable=False),
        Column("created_at", DateTime, server_default=func.now()),
        Column("deleted_at", DateTime, nullable=True),
    )

    task_time_logs = Table(
        "task_time_logs", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("task_id", Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("hours", Float, nullable=False),
        Column("description", Text, nullable=True),
        Column("logged_date", DateTime, nullable=False),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    task_dependencies = Table(
        "task_dependencies", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("blocking_task_id", Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        Column("blocked_task_id", Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        Column("dependency_type", String(50), nullable=False, default="blocks"),
        Column("created_at", DateTime, server_default=func.now()),
        Column("created_by", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )

    checklists = Table(
        "checklists", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("task_id", Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        Column("title", String(255), nullable=False),
        Column("position", Integer, nullable=False, default=0),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )
    
    checklist_items = Table(
        "checklist_items", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("checklist_id", Integer, ForeignKey("checklists.id", ondelete="CASCADE"), nullable=False),
        Column("content", String(500), nullable=False),
        Column("is_completed", Boolean, nullable=False, default=False),
        Column("position", Integer, nullable=False, default=0),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    # =====================================================================
    # Cluster D — Documents & Knowledge Base (8 tables)
    # =====================================================================
    documents = Table(
        "documents", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("project_id", Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("title", String(255), nullable=False),
        Column("content_html", Text, nullable=True),
        Column("author_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("status", String(50), nullable=False, default="draft"),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
        Column("deleted_at", DateTime, nullable=True),
    )

    document_versions = Table(
        "document_versions", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("document_id", Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        Column("version_number", Integer, nullable=False),
        Column("content_html", Text, nullable=False),
        Column("author_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("commit_message", String(255), nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
    )

    document_comments = Table(
        "document_comments", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("document_id", Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        Column("author_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("content", Text, nullable=False),
        Column("selection_text", Text, nullable=True),
        Column("resolved_at", DateTime, nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    document_tags = Table(
        "document_tags", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("name", String(50), nullable=False),
        Column("created_at", DateTime, server_default=func.now()),
    )

    document_tag_map = Table(
        "document_tag_map", metadata,
        Column("document_id", Integer, ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        Column("tag_id", Integer, ForeignKey("document_tags.id", ondelete="CASCADE"), primary_key=True),
        Column("added_at", DateTime, server_default=func.now()),
    )

    wikis = Table(
        "wikis", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("name", String(255), nullable=False),
        Column("description", Text, nullable=True),
        Column("is_public", Boolean, nullable=False, default=False),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    wiki_pages = Table(
        "wiki_pages", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("wiki_id", Integer, ForeignKey("wikis.id", ondelete="CASCADE"), nullable=False),
        Column("parent_page_id", Integer, ForeignKey("wiki_pages.id", ondelete="CASCADE"), nullable=True),
        Column("title", String(255), nullable=False),
        Column("slug", String(255), nullable=False),
        Column("content_markdown", Text, nullable=True),
        Column("author_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
        Column("deleted_at", DateTime, nullable=True),
    )

    wiki_page_revisions = Table(
        "wiki_page_revisions", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("wiki_page_id", Integer, ForeignKey("wiki_pages.id", ondelete="CASCADE"), nullable=False),
        Column("content_markdown", Text, nullable=False),
        Column("author_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
    )

    # =====================================================================
    # Cluster E — Notifications & Messaging (8 tables)
    # =====================================================================
    notifications = Table(
        "notifications", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("type", String(100), nullable=False),
        Column("title", String(255), nullable=False),
        Column("body", Text, nullable=True),
        Column("action_url", String(1024), nullable=True),
        Column("is_read", Boolean, nullable=False, default=False),
        Column("read_at", DateTime, nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
    )

    notification_preferences = Table(
        "notification_preferences", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("channel", String(50), nullable=False), # email, push, in_app
        Column("notification_type", String(100), nullable=False),
        Column("is_enabled", Boolean, nullable=False, default=True),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    channels = Table(
        "channels", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("name", String(100), nullable=False),
        Column("description", Text, nullable=True),
        Column("is_private", Boolean, nullable=False, default=False),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
        Column("deleted_at", DateTime, nullable=True),
    )

    channel_members = Table(
        "channel_members", metadata,
        Column("channel_id", Integer, ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        Column("role", String(50), nullable=False, default="member"),
        Column("joined_at", DateTime, server_default=func.now()),
        Column("last_read_at", DateTime, nullable=True),
    )

    messages = Table(
        "messages", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("channel_id", Integer, ForeignKey("channels.id", ondelete="CASCADE"), nullable=False),
        Column("sender_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("content", Text, nullable=False),
        Column("thread_id", Integer, ForeignKey("messages.id", ondelete="CASCADE"), nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
        Column("deleted_at", DateTime, nullable=True),
    )

    message_reactions = Table(
        "message_reactions", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("message_id", Integer, ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("emoji", String(50), nullable=False),
        Column("created_at", DateTime, server_default=func.now()),
    )

    direct_messages = Table(
        "direct_messages", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("sender_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("recipient_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("content", Text, nullable=False),
        Column("is_read", Boolean, nullable=False, default=False),
        Column("read_at", DateTime, nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
        Column("deleted_at", DateTime, nullable=True),
    )

    webhooks = Table(
        "webhooks", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("url", String(1024), nullable=False),
        Column("secret", String(255), nullable=True),
        Column("events", String(500), nullable=False), # comma separated list
        Column("is_active", Boolean, nullable=False, default=True),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    # =====================================================================
    # Cluster F — Billing & Subscriptions (12 tables)
    # =====================================================================
    plans = Table(
        "plans", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("stripe_product_id", String(255), nullable=True),
        Column("name", String(100), nullable=False),
        Column("description", Text, nullable=True),
        Column("interval", String(20), nullable=False), # month, year
        Column("price", Float, nullable=False),
        Column("currency", String(3), nullable=False, default="USD"),
        Column("is_active", Boolean, nullable=False, default=True),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    plan_features = Table(
        "plan_features", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("plan_id", Integer, ForeignKey("plans.id", ondelete="CASCADE"), nullable=False),
        Column("feature_key", String(100), nullable=False),
        Column("feature_value", String(255), nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
    )

    subscriptions = Table(
        "subscriptions", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("plan_id", Integer, ForeignKey("plans.id", ondelete="RESTRICT"), nullable=False),
        Column("stripe_subscription_id", String(255), nullable=True),
        Column("status", String(50), nullable=False), # active, past_due, canceled
        Column("current_period_start", DateTime, nullable=False),
        Column("current_period_end", DateTime, nullable=False),
        Column("cancel_at_period_end", Boolean, nullable=False, default=False),
        Column("canceled_at", DateTime, nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    subscription_items = Table(
        "subscription_items", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("subscription_id", Integer, ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False),
        Column("stripe_subscription_item_id", String(255), nullable=True),
        Column("stripe_price_id", String(255), nullable=True),
        Column("quantity", Integer, nullable=False, default=1),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    invoices = Table(
        "invoices", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("subscription_id", Integer, ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True),
        Column("stripe_invoice_id", String(255), nullable=True),
        Column("number", String(100), nullable=True),
        Column("status", String(50), nullable=False), # draft, open, paid, void, uncollectible
        Column("currency", String(3), nullable=False, default="USD"),
        Column("subtotal", Float, nullable=False),
        Column("tax", Float, nullable=False, default=0.0),
        Column("total", Float, nullable=False),
        Column("amount_paid", Float, nullable=False, default=0.0),
        Column("amount_due", Float, nullable=False),
        Column("invoice_date", DateTime, nullable=False),
        Column("due_date", DateTime, nullable=True),
        Column("paid_at", DateTime, nullable=True),
        Column("invoice_pdf", String(1024), nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    invoice_line_items = Table(
        "invoice_line_items", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("invoice_id", Integer, ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False),
        Column("stripe_invoice_item_id", String(255), nullable=True),
        Column("description", String(255), nullable=False),
        Column("amount", Float, nullable=False),
        Column("currency", String(3), nullable=False, default="USD"),
        Column("quantity", Integer, nullable=False, default=1),
        Column("period_start", DateTime, nullable=True),
        Column("period_end", DateTime, nullable=True),
    )

    payments = Table(
        "payments", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("invoice_id", Integer, ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("stripe_payment_intent_id", String(255), nullable=True),
        Column("amount", Float, nullable=False),
        Column("currency", String(3), nullable=False, default="USD"),
        Column("status", String(50), nullable=False), # succeeded, pending, failed
        Column("payment_method_type", String(50), nullable=True), # card, bank_transfer
        Column("receipt_url", String(1024), nullable=True),
        Column("error_message", Text, nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    payment_methods = Table(
        "payment_methods", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("stripe_payment_method_id", String(255), nullable=True),
        Column("type", String(50), nullable=False), # card
        Column("card_brand", String(50), nullable=True),
        Column("card_last4", String(4), nullable=True),
        Column("card_exp_month", Integer, nullable=True),
        Column("card_exp_year", Integer, nullable=True),
        Column("is_default", Boolean, nullable=False, default=False),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    coupons = Table(
        "coupons", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("stripe_coupon_id", String(255), nullable=True),
        Column("code", String(50), nullable=False, unique=True),
        Column("name", String(100), nullable=False),
        Column("amount_off", Float, nullable=True),
        Column("percent_off", Float, nullable=True),
        Column("currency", String(3), nullable=True),
        Column("duration", String(20), nullable=False), # once, repeating, forever
        Column("duration_in_months", Integer, nullable=True),
        Column("max_redemptions", Integer, nullable=True),
        Column("times_redeemed", Integer, nullable=False, default=0),
        Column("valid_from", DateTime, nullable=True),
        Column("valid_until", DateTime, nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    coupon_redemptions = Table(
        "coupon_redemptions", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("coupon_id", Integer, ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False),
        Column("subscription_id", Integer, ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False),
        Column("redeemed_at", DateTime, server_default=func.now()),
    )

    usage_records = Table(
        "usage_records", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("subscription_item_id", Integer, ForeignKey("subscription_items.id", ondelete="CASCADE"), nullable=False),
        Column("stripe_usage_record_id", String(255), nullable=True),
        Column("quantity", Integer, nullable=False),
        Column("timestamp", DateTime, nullable=False),
        Column("action", String(20), nullable=False, default="set"), # set, increment
        Column("created_at", DateTime, server_default=func.now()),
    )

    credits = Table(
        "credits", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("amount", Float, nullable=False),
        Column("currency", String(3), nullable=False, default="USD"),
        Column("description", String(255), nullable=True),
        Column("created_by", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
    )

    # =====================================================================
    # Cluster G — Integrations & API (8 tables)
    # =====================================================================
    api_keys = Table(
        "api_keys", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("created_by", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("name", String(100), nullable=False),
        Column("key_prefix", String(20), nullable=False),
        Column("key_hash", String(255), nullable=False),
        Column("last_used_at", DateTime, nullable=True),
        Column("expires_at", DateTime, nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
        Column("revoked_at", DateTime, nullable=True),
    )

    api_key_scopes = Table(
        "api_key_scopes", metadata,
        Column("api_key_id", Integer, ForeignKey("api_keys.id", ondelete="CASCADE"), primary_key=True),
        Column("scope", String(100), nullable=False, primary_key=True),
        Column("created_at", DateTime, server_default=func.now()),
    )

    integrations = Table(
        "integrations", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("name", String(100), nullable=False, unique=True),
        Column("provider", String(100), nullable=False),
        Column("description", Text, nullable=True),
        Column("logo_url", String(255), nullable=True),
        Column("is_active", Boolean, nullable=False, default=True),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    org_integrations = Table(
        "org_integrations", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("integration_id", Integer, ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False),
        Column("connected_by", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("settings_json", Text, nullable=True),
        Column("credentials_enc", Text, nullable=True),
        Column("status", String(50), nullable=False, default="active"),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    integration_events = Table(
        "integration_events", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("org_integration_id", Integer, ForeignKey("org_integrations.id", ondelete="CASCADE"), nullable=False),
        Column("event_type", String(100), nullable=False),
        Column("payload", Text, nullable=True),
        Column("status", String(50), nullable=False), # success, failed
        Column("error_message", Text, nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
    )

    webhooks_deliveries = Table(
        "webhooks_deliveries", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("webhook_id", Integer, ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False),
        Column("event_type", String(100), nullable=False),
        Column("payload", Text, nullable=False),
        Column("request_headers", Text, nullable=True),
        Column("response_status", Integer, nullable=True),
        Column("response_body", Text, nullable=True),
        Column("duration_ms", Integer, nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
    )

    oauth_apps = Table(
        "oauth_apps", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("name", String(100), nullable=False),
        Column("description", Text, nullable=True),
        Column("homepage_url", String(255), nullable=False),
        Column("callback_url", String(1024), nullable=False),
        Column("client_id", String(255), nullable=False, unique=True),
        Column("client_secret", String(255), nullable=False),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    oauth_app_tokens = Table(
        "oauth_app_tokens", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("oauth_app_id", Integer, ForeignKey("oauth_apps.id", ondelete="CASCADE"), nullable=False),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("access_token", String(255), nullable=False, unique=True),
        Column("refresh_token", String(255), nullable=True, unique=True),
        Column("scopes", String(500), nullable=False),
        Column("expires_at", DateTime, nullable=False),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    # =====================================================================
    # Cluster H — Analytics & Reporting (9 tables)
    # =====================================================================
    events = Table(
        "events", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("event_name", String(255), nullable=False),
        Column("properties_json", Text, nullable=True),
        Column("platform", String(50), nullable=True),
        Column("ip_address", String(45), nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
    )

    page_views = Table(
        "page_views", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("session_id", String(255), nullable=False),
        Column("url", String(1024), nullable=False),
        Column("path", String(500), nullable=False),
        Column("referrer", String(1024), nullable=True),
        Column("user_agent", Text, nullable=True),
        Column("duration_seconds", Integer, nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
    )

    feature_usage = Table(
        "feature_usage", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("feature_key", String(100), nullable=False),
        Column("usage_count", Integer, nullable=False, default=1),
        Column("first_used_at", DateTime, server_default=func.now()),
        Column("last_used_at", DateTime, onupdate=func.now(), server_default=func.now()),
    )

    reports = Table(
        "reports", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("creator_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("name", String(255), nullable=False),
        Column("description", Text, nullable=True),
        Column("report_type", String(100), nullable=False),
        Column("query_config_json", Text, nullable=False),
        Column("is_public", Boolean, nullable=False, default=False),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    report_filters = Table(
        "report_filters", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("report_id", Integer, ForeignKey("reports.id", ondelete="CASCADE"), nullable=False),
        Column("field_name", String(100), nullable=False),
        Column("operator", String(50), nullable=False),
        Column("value", String(255), nullable=False),
        Column("created_at", DateTime, server_default=func.now()),
    )

    dashboards = Table(
        "dashboards", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("creator_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("name", String(255), nullable=False),
        Column("description", Text, nullable=True),
        Column("is_shared", Boolean, nullable=False, default=False),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    dashboard_widgets = Table(
        "dashboard_widgets", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("dashboard_id", Integer, ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False),
        Column("report_id", Integer, ForeignKey("reports.id", ondelete="CASCADE"), nullable=False),
        Column("title", String(255), nullable=True),
        Column("widget_type", String(50), nullable=False), # bar_chart, line_chart, metric
        Column("position_x", Integer, nullable=False, default=0),
        Column("position_y", Integer, nullable=False, default=0),
        Column("width", Integer, nullable=False, default=1),
        Column("height", Integer, nullable=False, default=1),
        Column("config_json", Text, nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    custom_fields = Table(
        "custom_fields", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("project_id", Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        Column("name", String(100), nullable=False),
        Column("field_type", String(50), nullable=False), # text, number, date, select
        Column("options_json", Text, nullable=True),
        Column("is_required", Boolean, nullable=False, default=False),
        Column("position", Integer, nullable=False, default=0),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    custom_field_values = Table(
        "custom_field_values", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("task_id", Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        Column("custom_field_id", Integer, ForeignKey("custom_fields.id", ondelete="CASCADE"), nullable=False),
        Column("text_value", Text, nullable=True),
        Column("number_value", Float, nullable=True),
        Column("date_value", DateTime, nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    # =====================================================================
    # Cluster I — Support & Feedback (9 tables)
    # =====================================================================
    support_tickets = Table(
        "support_tickets", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True),
        Column("requester_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("assignee_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("subject", String(255), nullable=False),
        Column("description", Text, nullable=False),
        Column("status", String(50), nullable=False, default="open"), # open, pending, resolved, closed
        Column("priority", String(50), nullable=False, default="normal"),
        Column("category", String(100), nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
        Column("resolved_at", DateTime, nullable=True),
    )

    ticket_messages = Table(
        "ticket_messages", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("ticket_id", Integer, ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False),
        Column("author_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("content", Text, nullable=False),
        Column("is_internal", Boolean, nullable=False, default=False),
        Column("created_at", DateTime, server_default=func.now()),
    )

    ticket_tags = Table(
        "ticket_tags", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("name", String(50), nullable=False, unique=True),
        Column("created_at", DateTime, server_default=func.now()),
    )

    ticket_tag_map = Table(
        "ticket_tag_map", metadata,
        Column("ticket_id", Integer, ForeignKey("support_tickets.id", ondelete="CASCADE"), primary_key=True),
        Column("tag_id", Integer, ForeignKey("ticket_tags.id", ondelete="CASCADE"), primary_key=True),
        Column("added_at", DateTime, server_default=func.now()),
    )

    feedback = Table(
        "feedback", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("type", String(50), nullable=False), # nps, csat, general
        Column("score", Integer, nullable=True),
        Column("comment", Text, nullable=True),
        Column("url", String(1024), nullable=True),
        Column("browser", String(255), nullable=True),
        Column("os", String(255), nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
    )

    feature_requests = Table(
        "feature_requests", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("requester_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True),
        Column("title", String(255), nullable=False),
        Column("description", Text, nullable=False),
        Column("status", String(50), nullable=False, default="under_review"), # planned, in_progress, completed, declined
        Column("vote_count", Integer, nullable=False, default=1),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    feature_votes = Table(
        "feature_votes", metadata,
        Column("feature_request_id", Integer, ForeignKey("feature_requests.id", ondelete="CASCADE"), primary_key=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        Column("created_at", DateTime, server_default=func.now()),
    )

    announcements = Table(
        "announcements", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("author_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("title", String(255), nullable=False),
        Column("content", Text, nullable=False),
        Column("target_audience", String(100), nullable=False, default="all"),
        Column("target_organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True),
        Column("status", String(50), nullable=False, default="draft"), # draft, published
        Column("published_at", DateTime, nullable=True),
        Column("expires_at", DateTime, nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    changelog_entries = Table(
        "changelog_entries", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("author_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("version", String(50), nullable=False),
        Column("title", String(255), nullable=False),
        Column("content", Text, nullable=False),
        Column("is_published", Boolean, nullable=False, default=False),
        Column("published_at", DateTime, nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    # =====================================================================
    # Cluster J — Marketing & Campaigns (10 tables)
    # =====================================================================
    campaigns = Table(
        "campaigns", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("name", String(255), nullable=False),
        Column("description", Text, nullable=True),
        Column("status", String(50), nullable=False, default="draft"), # draft, active, paused, completed
        Column("start_date", DateTime, nullable=True),
        Column("end_date", DateTime, nullable=True),
        Column("budget", Float, nullable=True),
        Column("currency", String(3), nullable=False, default="USD"),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    campaign_audiences = Table(
        "campaign_audiences", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("campaign_id", Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        Column("name", String(255), nullable=False),
        Column("criteria_json", Text, nullable=False),
        Column("estimated_size", Integer, nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
    )

    campaign_assets = Table(
        "campaign_assets", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("campaign_id", Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        Column("asset_type", String(50), nullable=False), # image, video, email_template
        Column("url", String(1024), nullable=False),
        Column("metadata_json", Text, nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
    )

    leads = Table(
        "leads", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("campaign_id", Integer, ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True),
        Column("email", String(255), nullable=False),
        Column("first_name", String(100), nullable=True),
        Column("last_name", String(100), nullable=True),
        Column("company_name", String(255), nullable=True),
        Column("status", String(50), nullable=False, default="new"), # new, contacted, qualified, lost
        Column("score", Integer, nullable=False, default=0),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    lead_activities = Table(
        "lead_activities", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("lead_id", Integer, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        Column("activity_type", String(100), nullable=False), # email_opened, link_clicked, form_submitted
        Column("description", Text, nullable=True),
        Column("ip_address", String(45), nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
    )

    affiliate_programs = Table(
        "affiliate_programs", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("organization_id", Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        Column("name", String(255), nullable=False),
        Column("commission_rate", Float, nullable=False),
        Column("cookie_days", Integer, nullable=False, default=30),
        Column("is_active", Boolean, nullable=False, default=True),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    affiliates = Table(
        "affiliates", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("program_id", Integer, ForeignKey("affiliate_programs.id", ondelete="CASCADE"), nullable=False),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("referral_code", String(50), nullable=False, unique=True),
        Column("status", String(50), nullable=False, default="pending"), # pending, approved, rejected
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    affiliate_clicks = Table(
        "affiliate_clicks", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("affiliate_id", Integer, ForeignKey("affiliates.id", ondelete="CASCADE"), nullable=False),
        Column("ip_address", String(45), nullable=True),
        Column("user_agent", Text, nullable=True),
        Column("landing_page_url", String(1024), nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
    )

    affiliate_referrals = Table(
        "affiliate_referrals", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("affiliate_id", Integer, ForeignKey("affiliates.id", ondelete="CASCADE"), nullable=False),
        Column("referred_user_id", Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        Column("status", String(50), nullable=False, default="pending"), # pending, qualified, rejected
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    affiliate_payouts = Table(
        "affiliate_payouts", metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("affiliate_id", Integer, ForeignKey("affiliates.id", ondelete="CASCADE"), nullable=False),
        Column("amount", Float, nullable=False),
        Column("currency", String(3), nullable=False, default="USD"),
        Column("status", String(50), nullable=False, default="pending"), # pending, processing, paid, failed
        Column("payout_method", String(100), nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
        Column("paid_at", DateTime, nullable=True),
    )

    print(f"Creating {len(metadata.tables)} tables in PostgreSQL...")
    metadata.drop_all(engine)
    metadata.create_all(engine)
    
    print("✓ Schema creation complete!")
    print(f"  Total Tables: {len(metadata.tables)}")
    
    # Optional: Basic count of FKs
    fk_count = sum(len(t.foreign_keys) for t in metadata.tables.values())
    print(f"  Total FKs:    {fk_count}")

if __name__ == "__main__":
    eng = get_engine()
    create_large_schema(eng)
