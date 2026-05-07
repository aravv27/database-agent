"""
workspace.py — Multi-database workspace manager.

Manages multiple database projects (SQLite and PostgreSQL) under the databases/ folder.
Each project has its own engine, graph, cache, descriptions, and embeddings.

Structure:
  databases/
    {name}/
      project.json         # {"dialect": "sqlite"} or {"dialect": "postgresql"}
      db.sqlite            # SQLite only
      schema_cache.json
"""

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from typing import Optional

import networkx as nx
from sqlalchemy import create_engine, event

from graph_builder import build_graph
from cache import (
    compute_all_hashes,
    get_stale_tables,
    load_cache,
    save_cache,
    serialize_graph,
    encode_embedding,
    decode_embedding,
)
from context_engine import generate_all_descriptions
from retrieval import embed_text, HybridRetriever

ROOT = os.path.dirname(os.path.abspath(__file__))
DATABASES_DIR = os.path.join(ROOT, "databases")


# ── Data model ─────────────────────────────────────────────────────────────

@dataclass
class DatabaseProject:
    """All runtime state for one database project."""
    name: str
    db_path: str            # SQLite: file path. Postgres: full connection URL
    cache_path: str
    dialect: str            # "sqlite" or "postgresql"
    engine: object          # SQLAlchemy Engine
    graph: nx.DiGraph
    descriptions: dict      # {table_name: {description, business_role}}
    embeddings: dict        # {table_name: np.array}
    hashes: dict            # {table_name: sha256_hex}
    retriever: object = None  # HybridRetriever (BM25 + embedding)
    table_count: int = 0


# ── Project config helpers ─────────────────────────────────────────────────

def _read_project_config(project_dir: str) -> dict:
    """Read project.json from a project folder. Returns defaults if missing."""
    config_path = os.path.join(project_dir, "project.json")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    # Legacy project — no config file means SQLite
    return {"dialect": "sqlite"}


def _write_project_config(project_dir: str, dialect: str):
    """Write project.json to a project folder."""
    config_path = os.path.join(project_dir, "project.json")
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump({"dialect": dialect}, f, indent=2)


# ── FK pragma ──────────────────────────────────────────────────────────────

def _enable_fk_support(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def make_engine(db_path: str, dialect: str = "sqlite"):
    """
    Create a SQLAlchemy engine.

    For SQLite: db_path is a file path, engine gets FK pragma listener.
    For PostgreSQL: db_path is the full connection URL, FKs enforced natively.
    """
    if dialect == "postgresql":
        engine = create_engine(db_path, echo=False)
    else:
        engine = create_engine(f"sqlite:///{db_path}", echo=False)
        event.listen(engine, "connect", _enable_fk_support)
    return engine


# ── Pipeline ───────────────────────────────────────────────────────────────

def _run_pipeline(name: str, db_path: str, cache_path: str,
                  dialect: str = "sqlite", engine=None,
                  silent: bool = False) -> DatabaseProject:
    """
    Full startup pipeline for a database:
      reflect → hash → cache compare → describe stale → embed stale → save cache
    Returns a fully loaded DatabaseProject.
    """
    t0 = time.time()

    if engine is None:
        engine = make_engine(db_path, dialect)

    # Graph
    if not silent:
        print(f"  Building graph for [{name}]...")
    graph = build_graph(engine)

    # Cache + hashes
    cache = load_cache(cache_path)
    current_hashes = compute_all_hashes(graph)
    cached_hashes = cache.get("hashes", {})
    stale = get_stale_tables(current_hashes, cached_hashes)

    stale_in_graph = stale & set(graph.nodes)
    cached_count = len(graph.nodes) - len(stale_in_graph)

    if stale_in_graph and not silent:
        print(f"  {len(stale_in_graph)} table(s) changed, {cached_count} from cache")
    elif not silent:
        print(f"  All {len(graph.nodes)} tables loaded from cache")

    # Descriptions
    if stale:
        if not silent:
            print(f"  Generating descriptions for {len(stale_in_graph)} table(s)...")
        descriptions = generate_all_descriptions(
            graph,
            stale_tables=stale,
            cached_descriptions=cache.get("descriptions", {}),
        )
    else:
        descriptions = cache.get("descriptions", {})

    # Embeddings
    embeddings = {}
    for t, b64 in cache.get("embeddings", {}).items():
        if t not in stale and t in graph.nodes:
            embeddings[t] = decode_embedding(b64)

    if stale_in_graph:
        if not silent:
            print(f"  Computing embeddings...")
        for t in sorted(stale_in_graph):
            desc_text = descriptions.get(t, {}).get("description", "")
            cols = ", ".join(c["name"] for c in graph.nodes[t]["columns"])
            embeddings[t] = embed_text(f"{t}: {desc_text}. Columns: {cols}")

    # Build hybrid retriever (BM25 + embedding, instantaneous to build)
    retriever = HybridRetriever(graph, descriptions, embeddings)

    # Save cache
    save_cache(
        {
            "hashes": current_hashes,
            "descriptions": descriptions,
            "embeddings": {t: encode_embedding(v) for t, v in embeddings.items()},
            "graph": serialize_graph(graph),
        },
        cache_path,
    )

    elapsed = time.time() - t0
    if not silent:
        rebuilt = len(stale_in_graph)
        print(f"  Ready [{name}]: {rebuilt} rebuilt, {cached_count} cached. ({elapsed:.2f}s)")

    return DatabaseProject(
        name=name,
        db_path=db_path,
        cache_path=cache_path,
        dialect=dialect,
        engine=engine,
        graph=graph,
        descriptions=descriptions,
        embeddings=embeddings,
        hashes=current_hashes,
        retriever=retriever,
        table_count=len(graph.nodes),
    )


# ── Workspace ──────────────────────────────────────────────────────────────

class Workspace:
    """
    Manages multiple database projects.

    Usage:
        ws = Workspace()
        ws.load("ecommerce")   # runs full pipeline
        ws.set_active("ecommerce")
        project = ws.active    # current DatabaseProject
    """

    def __init__(self):
        os.makedirs(DATABASES_DIR, exist_ok=True)
        self._projects: dict[str, DatabaseProject] = {}
        self._active: Optional[str] = None

    # ── Discovery ──

    def list_databases(self) -> list[str]:
        """List all database folders in databases/."""
        names = []
        for entry in sorted(os.scandir(DATABASES_DIR), key=lambda e: e.name):
            if entry.is_dir():
                db_file = os.path.join(entry.path, "db.sqlite")
                config_file = os.path.join(entry.path, "project.json")
                if os.path.exists(db_file) or os.path.exists(config_file):
                    names.append(entry.name)
        return names

    def project_dir(self, name: str) -> str:
        return os.path.join(DATABASES_DIR, name)

    def db_path(self, name: str) -> str:
        """Return the SQLite file path for a project."""
        return os.path.join(self.project_dir(name), "db.sqlite")

    def cache_path(self, name: str) -> str:
        return os.path.join(self.project_dir(name), "schema_cache.json")

    def get_dialect(self, name: str) -> str:
        """Get the dialect for a project by reading its config."""
        config = _read_project_config(self.project_dir(name))
        return config.get("dialect", "sqlite")

    def connection_string(self, name: str) -> str:
        """
        Get the connection string / path for a project.

        SQLite: returns the local file path (e.g., databases/ecommerce/db.sqlite)
        PostgreSQL: returns the full URL (e.g., postgresql://user:pass@host:5432/name)
        """
        dialect = self.get_dialect(name)
        if dialect == "postgresql":
            from dotenv import load_dotenv
            load_dotenv()
            postgres_url = os.getenv("POSTGRES_URL", "")
            if not postgres_url:
                raise ValueError("POSTGRES_URL not set in .env")
            return f"{postgres_url}/{name}"
        return self.db_path(name)

    def exists(self, name: str) -> bool:
        """Check if a project exists (has either db.sqlite or project.json)."""
        proj_dir = self.project_dir(name)
        if not os.path.isdir(proj_dir):
            return False
        return (
            os.path.exists(os.path.join(proj_dir, "db.sqlite"))
            or os.path.exists(os.path.join(proj_dir, "project.json"))
        )

    # ── Create ──

    def create_empty(self, name: str, dialect: str = "sqlite") -> str:
        """
        Create a new database project.

        For SQLite: creates a local db.sqlite file.
        For PostgreSQL: creates a database on the configured server.
        Returns the connection string / path.
        """
        proj_dir = self.project_dir(name)
        os.makedirs(proj_dir, exist_ok=True)

        if dialect == "postgresql":
            from dotenv import load_dotenv
            load_dotenv()
            postgres_url = os.getenv("POSTGRES_URL", "")
            if not postgres_url:
                raise ValueError("POSTGRES_URL not set in .env")

            # Connect to the default 'postgres' database to create the new one
            base_engine = create_engine(
                f"{postgres_url}/postgres", echo=False,
                isolation_level="AUTOCOMMIT",
            )
            with base_engine.connect() as conn:
                from sqlalchemy import text as _text
                result = conn.execute(
                    _text("SELECT 1 FROM pg_database WHERE datname = :dbname"),
                    {"dbname": name},
                )
                if not result.fetchone():
                    # Database names are identifiers — use quotes for safety
                    conn.execute(_text(f'CREATE DATABASE "{name}"'))
            base_engine.dispose()

            _write_project_config(proj_dir, "postgresql")
            conn_str = f"{postgres_url}/{name}"
            print(f"  Created PostgreSQL database: {name}")
            return conn_str

        # SQLite (default)
        db_file = self.db_path(name)
        engine = make_engine(db_file, "sqlite")
        with engine.connect() as conn:
            from sqlalchemy import text as _text
            conn.execute(_text("SELECT 1"))
        engine.dispose()
        _write_project_config(proj_dir, "sqlite")
        print(f"  Created empty database: databases/{name}/db.sqlite")
        return db_file

    def get_engine_for(self, name: str):
        """Get (or create) an engine for an existing or just-created DB."""
        if name in self._projects:
            return self._projects[name].engine
        dialect = self.get_dialect(name)
        conn_str = self.connection_string(name)
        return make_engine(conn_str, dialect)

    # ── Load ──

    def load(self, name: str, engine=None, silent: bool = False) -> DatabaseProject:
        """Run the full pipeline for a database and store it."""
        if not self.exists(name):
            raise ValueError(f"No database named '{name}'. Use 'create {name}' first.")
        dialect = self.get_dialect(name)
        conn_str = self.connection_string(name)
        project = _run_pipeline(
            name=name,
            db_path=conn_str,
            cache_path=self.cache_path(name),
            dialect=dialect,
            engine=engine,
            silent=silent,
        )
        self._projects[name] = project
        return project

    # ── Active ──

    @property
    def active(self) -> Optional[DatabaseProject]:
        if self._active and self._active in self._projects:
            return self._projects[self._active]
        return None

    def set_active(self, name: str) -> bool:
        if not self.exists(name):
            return False
        if name not in self._projects:
            self.load(name)
        self._active = name
        return True

    # ── Refresh ──

    def refresh_active(self) -> Optional[set]:
        """
        Re-reflect, re-hash, re-describe, re-embed the active database.
        Returns the set of stale (rebuilt) table names, or None if no active DB.
        """
        project = self.active
        if not project:
            return None

        old_hashes = project.hashes
        new_graph = build_graph(project.engine)
        new_hashes = compute_all_hashes(new_graph)
        stale = get_stale_tables(new_hashes, old_hashes)
        stale_in_graph = stale & set(new_graph.nodes)

        if not stale_in_graph:
            project.graph = new_graph
            project.hashes = new_hashes
            return set()

        # Re-describe stale
        project.descriptions = generate_all_descriptions(
            new_graph,
            stale_tables=stale,
            cached_descriptions=project.descriptions,
        )

        # Re-embed stale
        for t in sorted(stale_in_graph):
            desc_text = project.descriptions.get(t, {}).get("description", "")
            cols = ", ".join(c["name"] for c in new_graph.nodes[t]["columns"])
            project.embeddings[t] = embed_text(f"{t}: {desc_text}. Columns: {cols}")

        # Remove embeddings for dropped tables
        for t in list(project.embeddings):
            if t not in new_graph.nodes:
                del project.embeddings[t]

        # Rebuild hybrid retriever with updated data
        project.retriever = HybridRetriever(new_graph, project.descriptions, project.embeddings)

        # Update project state
        project.graph = new_graph
        project.hashes = new_hashes
        project.table_count = len(new_graph.nodes)

        # Save cache
        save_cache(
            {
                "hashes": new_hashes,
                "descriptions": project.descriptions,
                "embeddings": {t: encode_embedding(v) for t, v in project.embeddings.items()},
                "graph": serialize_graph(new_graph),
            },
            project.cache_path,
        )

        return stale_in_graph

    # ── Migration helper ──

    def migrate_legacy(self):
        """
        Move legacy test.db + schema_cache.json from project root
        into databases/ecommerce/ on first run.
        """
        legacy_db = os.path.join(ROOT, "test.db")
        legacy_cache = os.path.join(ROOT, "schema_cache.json")

        if not os.path.exists(legacy_db):
            return

        target_dir = self.project_dir("ecommerce")
        os.makedirs(target_dir, exist_ok=True)
        target_db = self.db_path("ecommerce")
        target_cache = self.cache_path("ecommerce")

        if not os.path.exists(target_db):
            shutil.move(legacy_db, target_db)
            print("  Migrated test.db -> databases/ecommerce/db.sqlite")

        if os.path.exists(legacy_cache) and not os.path.exists(target_cache):
            shutil.move(legacy_cache, target_cache)
            print("  Migrated schema_cache.json -> databases/ecommerce/schema_cache.json")
