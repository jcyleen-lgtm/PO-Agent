"""Database access: one SQLAlchemy engine per process + raw psycopg2 connections for bulk writes."""

import psycopg2
from fastapi import HTTPException
from sqlalchemy import create_engine

from src.core.config import DB_URL

_engine = None



def get_engine():
    """The process-wide engine. Built once (before: a new engine and pool on every call)."""
    global _engine
    if _engine is not None:
        return _engine
    if not DB_URL:
        raise HTTPException(500, "SUPABASE_DB_URL not configured")
    # Force psycopg2 driver (Vercel's SQLAlchemy 2.x defaults to psycopg v3 which isn't installed)
    url = DB_URL
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    elif url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    _engine = create_engine(url, pool_pre_ping=True)
    return _engine


def get_raw_conn():
    """psycopg2 connection built from the SAME parsed URL as get_engine().
    Passing the raw URL string to psycopg2 breaks when the password contains / # ? (libpq then reads
    part of the password as the port: 'invalid integer value ... for connection option "port"')."""
    if not DB_URL:
        raise HTTPException(500, "SUPABASE_DB_URL not configured")
    from sqlalchemy.engine import make_url
    u = make_url(DB_URL)
    kw = {k: (v[0] if isinstance(v, tuple) else v) for k, v in u.query.items()}
    if u.host and "host" not in kw:
        kw["host"] = u.host
    if u.port and "port" not in kw:
        kw["port"] = u.port
    if u.username:
        kw["user"] = u.username
    if u.password is not None:
        kw["password"] = u.password
    if u.database:
        kw["dbname"] = u.database
    return psycopg2.connect(**kw)
