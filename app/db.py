from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = "sqlite:///./listing_tool.db"

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema():
    """Add backward-compatible columns when older SQLite files are reused."""

    inspector = inspect(engine)
    columns = {col["name"] for col in inspector.get_columns("listing_jobs")}
    with engine.begin() as conn:
        if "item_name" not in columns:
            conn.execute(text("ALTER TABLE listing_jobs ADD COLUMN item_name VARCHAR"))
        if "ean" not in columns:
            conn.execute(text("ALTER TABLE listing_jobs ADD COLUMN ean VARCHAR"))
        if "query" not in columns:
            conn.execute(text("ALTER TABLE listing_jobs ADD COLUMN query VARCHAR"))
        if "condition_id" not in columns:
            conn.execute(text("ALTER TABLE listing_jobs ADD COLUMN condition_id VARCHAR"))
        if "condition_name" not in columns:
            conn.execute(text("ALTER TABLE listing_jobs ADD COLUMN condition_name VARCHAR"))
        if "image_paths" not in columns:
            conn.execute(text("ALTER TABLE listing_jobs ADD COLUMN image_paths TEXT"))

    preview_cols = {col["name"] for col in inspector.get_columns("listing_previews")}
    with engine.begin() as conn:
        if "category_id" not in preview_cols:
            conn.execute(text("ALTER TABLE listing_previews ADD COLUMN category_id VARCHAR"))
        if "condition_id" not in preview_cols:
            conn.execute(text("ALTER TABLE listing_previews ADD COLUMN condition_id VARCHAR"))
        if "condition_name" not in preview_cols:
            conn.execute(text("ALTER TABLE listing_previews ADD COLUMN condition_name VARCHAR"))
        if "missing_required_aspects" not in preview_cols:
            conn.execute(text("ALTER TABLE listing_previews ADD COLUMN missing_required_aspects TEXT"))
        if "market_suggestions" not in preview_cols:
            conn.execute(text("ALTER TABLE listing_previews ADD COLUMN market_suggestions TEXT"))
        if "avg_price_suggestion" not in preview_cols:
            conn.execute(text("ALTER TABLE listing_previews ADD COLUMN avg_price_suggestion VARCHAR"))

    hist_cols = {col["name"] for col in inspector.get_columns("listing_history")}
    with engine.begin() as conn:
        if "category_id" not in hist_cols:
            conn.execute(text("ALTER TABLE listing_history ADD COLUMN category_id VARCHAR"))
        if "condition_id" not in hist_cols:
            conn.execute(text("ALTER TABLE listing_history ADD COLUMN condition_id VARCHAR"))
        if "condition_name" not in hist_cols:
            conn.execute(text("ALTER TABLE listing_history ADD COLUMN condition_name VARCHAR"))
        if "ebay_listing_id" not in hist_cols:
            conn.execute(text("ALTER TABLE listing_history ADD COLUMN ebay_listing_id VARCHAR"))
        if "price" not in hist_cols:
            conn.execute(text("ALTER TABLE listing_history ADD COLUMN price VARCHAR"))

    ebay_cols = {col["name"] for col in inspector.get_columns("ebay_auth")}
    with engine.begin() as conn:
        if "payment_policy_id" not in ebay_cols:
            conn.execute(text("ALTER TABLE ebay_auth ADD COLUMN payment_policy_id VARCHAR"))
        if "return_policy_id" not in ebay_cols:
            conn.execute(text("ALTER TABLE ebay_auth ADD COLUMN return_policy_id VARCHAR"))
        if "fulfillment_policies" not in ebay_cols:
            conn.execute(text("ALTER TABLE ebay_auth ADD COLUMN fulfillment_policies TEXT"))

    if "category_cache" not in inspector.get_table_names():
        Base.metadata.tables["category_cache"].create(bind=engine)
