"""
database.py — Creates a realistic SQLite schema with explicit FK constraints.

Schema:
  users → addresses
  users → orders → order_items → products
  products → categories
  orders → shipments

All FK constraints are declared explicitly in SQLAlchemy so that
graph_builder can reflect them automatically.
"""

import os
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
    event,
)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "databases", "ecommerce", "db.sqlite")
DB_URL = f"sqlite:///{DB_PATH}"


def _enable_fk_support(dbapi_conn, connection_record):
    """SQLite doesn't enforce FKs by default — we need this pragma."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def get_engine(db_path: str = None):
    path = db_path or DB_PATH
    engine = create_engine(f"sqlite:///{path}", echo=False)
    event.listen(engine, "connect", _enable_fk_support)
    return engine


def create_schema(engine=None, db_path: str = None):
    """Create all tables. Accepts an existing engine or a db_path. Returns the engine."""
    if engine is None:
        engine = get_engine(db_path)

    metadata = MetaData()

    # ── users ───────────────────────────────────────────────────────────
    Table(
        "users",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("email", String(255), nullable=False, unique=True),
        Column("name", String(255), nullable=False),
        Column("password_hash", String(255), nullable=False),
        Column("role", String(50), nullable=False, default="customer"),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
        Column("deleted_at", DateTime, nullable=True),
    )

    # ── categories ──────────────────────────────────────────────────────
    Table(
        "categories",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("name", String(100), nullable=False, unique=True),
        Column("slug", String(100), nullable=False, unique=True),
        Column("description", Text, nullable=True),
    )

    # ── products ────────────────────────────────────────────────────────
    Table(
        "products",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column(
            "category_id",
            Integer,
            ForeignKey("categories.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        Column("name", String(255), nullable=False),
        Column("description", Text, nullable=True),
        Column("price", Float, nullable=False),
        Column("stock_count", Integer, nullable=False, default=0),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
    )

    # ── addresses ───────────────────────────────────────────────────────
    Table(
        "addresses",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column(
            "user_id",
            Integer,
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        Column("street", String(255), nullable=False),
        Column("city", String(100), nullable=False),
        Column("state", String(100), nullable=True),
        Column("zip_code", String(20), nullable=False),
        Column("country", String(100), nullable=False, default="US"),
        Column("is_primary", Boolean, nullable=False, default=False),
    )

    # ── orders ──────────────────────────────────────────────────────────
    Table(
        "orders",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column(
            "user_id",
            Integer,
            ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        Column(
            "shipping_address_id",
            Integer,
            ForeignKey("addresses.id", ondelete="SET NULL"),
            nullable=True,
        ),
        Column("status", String(50), nullable=False, default="pending"),
        Column("total", Float, nullable=False, default=0.0),
        Column("notes", Text, nullable=True),
        Column("created_at", DateTime, server_default=func.now()),
        Column("updated_at", DateTime, onupdate=func.now()),
        Column("deleted_at", DateTime, nullable=True),
    )

    # ── order_items ─────────────────────────────────────────────────────
    Table(
        "order_items",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column(
            "order_id",
            Integer,
            ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        Column(
            "product_id",
            Integer,
            ForeignKey("products.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        Column("quantity", Integer, nullable=False, default=1),
        Column("unit_price", Float, nullable=False),
    )

    # ── shipments ───────────────────────────────────────────────────────
    Table(
        "shipments",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column(
            "order_id",
            Integer,
            ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        Column("carrier", String(100), nullable=False),
        Column("tracking_number", String(255), nullable=True),
        Column("status", String(50), nullable=False, default="preparing"),
        Column("shipped_at", DateTime, nullable=True),
        Column("delivered_at", DateTime, nullable=True),
    )

    metadata.create_all(engine)
    return engine


def seed_data(engine):
    """Insert minimal seed data for testing."""
    with engine.connect() as conn:
        # Check if data already exists
        result = conn.execute(Table("users", MetaData(), autoload_with=engine).select())
        if result.fetchone():
            return  # Already seeded

        conn.execute(
            Table("users", MetaData(), autoload_with=engine).insert(),
            [
                {"email": "alice@example.com", "name": "Alice Smith", "password_hash": "hash1", "role": "customer"},
                {"email": "bob@example.com", "name": "Bob Jones", "password_hash": "hash2", "role": "admin"},
                {"email": "carol@example.com", "name": "Carol White", "password_hash": "hash3", "role": "customer"},
            ],
        )

        conn.execute(
            Table("categories", MetaData(), autoload_with=engine).insert(),
            [
                {"name": "Electronics", "slug": "electronics", "description": "Electronic devices and gadgets"},
                {"name": "Books", "slug": "books", "description": "Physical and digital books"},
                {"name": "Clothing", "slug": "clothing", "description": "Apparel and accessories"},
            ],
        )

        conn.execute(
            Table("products", MetaData(), autoload_with=engine).insert(),
            [
                {"category_id": 1, "name": "Wireless Headphones", "price": 79.99, "stock_count": 150},
                {"category_id": 1, "name": "USB-C Hub", "price": 34.99, "stock_count": 300},
                {"category_id": 2, "name": "Python Crash Course", "price": 29.99, "stock_count": 500},
                {"category_id": 3, "name": "Cotton T-Shirt", "price": 19.99, "stock_count": 1000},
            ],
        )

        conn.execute(
            Table("addresses", MetaData(), autoload_with=engine).insert(),
            [
                {"user_id": 1, "street": "123 Main St", "city": "Portland", "state": "OR", "zip_code": "97201", "country": "US", "is_primary": True},
                {"user_id": 2, "street": "456 Oak Ave", "city": "Seattle", "state": "WA", "zip_code": "98101", "country": "US", "is_primary": True},
            ],
        )

        conn.execute(
            Table("orders", MetaData(), autoload_with=engine).insert(),
            [
                {"user_id": 1, "shipping_address_id": 1, "status": "completed", "total": 114.98},
                {"user_id": 1, "shipping_address_id": 1, "status": "pending", "total": 29.99},
                {"user_id": 2, "shipping_address_id": 2, "status": "shipped", "total": 19.99},
            ],
        )

        conn.execute(
            Table("order_items", MetaData(), autoload_with=engine).insert(),
            [
                {"order_id": 1, "product_id": 1, "quantity": 1, "unit_price": 79.99},
                {"order_id": 1, "product_id": 2, "quantity": 1, "unit_price": 34.99},
                {"order_id": 2, "product_id": 3, "quantity": 1, "unit_price": 29.99},
                {"order_id": 3, "product_id": 4, "quantity": 1, "unit_price": 19.99},
            ],
        )

        conn.execute(
            Table("shipments", MetaData(), autoload_with=engine).insert(),
            [
                {"order_id": 1, "carrier": "USPS", "tracking_number": "9400111899223456789012", "status": "delivered"},
                {"order_id": 3, "carrier": "FedEx", "tracking_number": "794644790132", "status": "in_transit"},
            ],
        )

        conn.commit()


if __name__ == "__main__":
    eng = create_schema()
    seed_data(eng)
    print(f"Database created and seeded at: {DB_PATH}")
