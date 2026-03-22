from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, Enum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./rooh_inventory.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class InventoryItem(Base):
    __tablename__ = "inventory_items"

    id            = Column(Integer, primary_key=True, index=True)
    name          = Column(String, unique=True, index=True, nullable=False)
    category      = Column(String, nullable=False)
    sub_category  = Column(String, nullable=True)
    department    = Column(String, nullable=False, default="kitchen")  # kitchen | bar | extras
    storage       = Column(String, nullable=True)
    unit          = Column(String, nullable=False)
    current_stock = Column(Float, nullable=False, default=0.0)
    reorder_level = Column(Float, nullable=True)
    critical_level= Column(Float, nullable=True)
    expiry_date   = Column(String, nullable=True)
    supplier      = Column(String, nullable=True)
    quality       = Column(String, nullable=True)
    last_updated  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by    = Column(String, nullable=True)


class StockMovement(Base):
    """Every IN (bill/procurement) and OUT (usage/telegram) is logged here."""
    __tablename__ = "stock_movements"

    id          = Column(Integer, primary_key=True, index=True)
    item_name   = Column(String, nullable=False, index=True)
    direction   = Column(String, nullable=False)   # "IN" | "OUT"
    quantity    = Column(Float, nullable=False)
    unit        = Column(String, nullable=False)
    source      = Column(String, nullable=True)    # "bill_scan" | "telegram" | "manual"
    entered_by  = Column(String, nullable=True)
    remarks     = Column(Text, nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow)


class BillScan(Base):
    """Stores OCR results from bill image uploads."""
    __tablename__ = "bill_scans"

    id            = Column(Integer, primary_key=True, index=True)
    raw_text      = Column(Text, nullable=False)
    parsed_items  = Column(Text, nullable=False)   # JSON list
    applied       = Column(Integer, default=0)     # 1 = stock updated from this bill
    uploaded_by   = Column(String, nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)


class Alert(Base):
    __tablename__ = "alerts"

    id         = Column(Integer, primary_key=True, index=True)
    item_name  = Column(String, nullable=False)
    alert_type = Column(String, nullable=False)  # "critical" | "reorder" | "expired" | "expiring_soon"
    message    = Column(Text, nullable=False)
    resolved   = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class QueryLog(Base):
    __tablename__ = "query_logs"

    id           = Column(Integer, primary_key=True, index=True)
    user_query   = Column(Text, nullable=False)
    ai_response  = Column(Text, nullable=False)
    source       = Column(String, default="api")  # "api" | "telegram"
    created_at   = Column(DateTime, default=datetime.utcnow)


def create_tables():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
