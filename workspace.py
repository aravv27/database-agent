"""
workspace.py — Multi-database workspace manager.

Manages multiple SQLite database projects under the databases/ folder.
Each project has its own engine, graph, cache, descriptions, and embeddings.

Structure:
  databases/
    {name}/
      db.sqlite
      schema_cache.json
"""

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
from retrieval import embed_text

ROOT = os.path.dirname(os.path.abspath(__file__))
DATABASES_DIR = os.path.join(ROOT, "databases")


# ── Data model ─────────────────────────────────────────────────────────────

@dataclass
class DatabaseProject:
    """All runtime state for one database project."""
    name: str
    db_path: str
    cache_path: str
    engine: object          # SQLAlchemy Engine
    graph: nx.DiGraph
    descriptions: dict      # {table_name: {description, business_role}}
    embeddings: dict        # {table_name: np.array}
    hashes: dict            # {table_name: sha256_hex}
    table_count: int = 0


# ── FK pragma ──────────────────────────────────────────────────────────────

def _enable_fk_support(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def make_engine(db_path: str):
    """Create a SQLAlchemy engine with FK support enabled."""
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    event.listen(engine, "connect", _enable_fk_support)
    return engine


# ── Pipeline ───────────────────────────────────────────────────────────────

def _run_pipeline(name: str, db_path: str, cache_path: str,
                  engine=None, silent: bool = False) -> DatabaseProject:
    """
    Full startup pipeline for a database:
      reflect → hash → cache compare → describe stale → embed stale → save cache
    Returns a fully loaded DatabaseProject.
    """
    t0 = time.time()

    if engine is None:
        engine = make_engine(db_path)

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
        engine=engine,
        graph=graph,
        descriptions=descriptions,
        embeddings=embeddings,
        hashes=current_hashes,
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
                if os.path.exists(db_file):
                    names.append(entry.name)
        return names

    def project_dir(self, name: str) -> str:
        return os.path.join(DATABASES_DIR, name)

    def db_path(self, name: str) -> str:
        return os.path.join(self.project_dir(name), "db.sqlite")

    def cache_path(self, name: str) -> str:
        return os.path.join(self.project_dir(name), "schema_cache.json")

    def exists(self, name: str) -> bool:
        return os.path.exists(self.db_path(name))

    # ── Create ──

    def create_empty(self, name: str) -> str:
        """Create an empty database project folder + sqlite file. Returns db_path."""
        proj_dir = self.project_dir(name)
        os.makedirs(proj_dir, exist_ok=True)
        db_file = self.db_path(name)
        # Must actually connect to force SQLite to create the file on disk
        engine = make_engine(db_file)
        with engine.connect() as conn:
            from sqlalchemy import text as _text
            conn.execute(_text("SELECT 1"))
        engine.dispose()
        print(f"  Created empty database: databases/{name}/db.sqlite")
        return db_file


    def get_engine_for(self, name: str):
        """Get (or create) an engine for an existing or just-created DB."""
        if name in self._projects:
            return self._projects[name].engine
        return make_engine(self.db_path(name))

    # ── Load ──

    def load(self, name: str, engine=None, silent: bool = False) -> DatabaseProject:
        """Run the full pipeline for a database and store it."""
        if not self.exists(name):
            raise ValueError(f"No database named '{name}'. Use 'create {name}' first.")
        project = _run_pipeline(
            name=name,
            db_path=self.db_path(name),
            cache_path=self.cache_path(name),
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
