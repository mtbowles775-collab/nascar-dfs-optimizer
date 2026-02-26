# ============================================================
# database.py — PostgreSQL connection via SQLAlchemy
# ============================================================

import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
# Format: postgresql://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres

if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set. Check your .env file.")

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,       # test connections before using them
    pool_size=10,             # max 10 persistent connections
    max_overflow=20,          # allow 20 extra connections under load
    pool_recycle=300,         # recycle connections every 5 min
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    """Dependency injected into every route that needs DB access."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
