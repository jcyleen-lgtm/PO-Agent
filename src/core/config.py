"""Process-level configuration: environment, paths, planning parameters.

Planning parameters (lead time, safety stock, thresholds …) stay in the top-level config.py so the
thesis rules have one home; this module only re-exports them next to the infrastructure settings.
"""

import os

from dotenv import load_dotenv

load_dotenv()

from config import settings as get_settings  # noqa: E402  (planning parameters, env overrides SW_*)

DB_URL = os.getenv("SUPABASE_DB_URL")
UPLOAD_DIR = os.path.join("/tmp", "uploads")      # Vercel: only /tmp is writable
MASTER_SKU_PATH = "MASTER_SKU.xlsx"
os.makedirs(UPLOAD_DIR, exist_ok=True)

__all__ = ["DB_URL", "UPLOAD_DIR", "MASTER_SKU_PATH", "get_settings"]
