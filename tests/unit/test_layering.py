"""Architecture guard: SQL lives only in src/repositories (plus the runtime DDL fallbacks in src/core/ddl.py,
which are infrastructure). Routers, services, parsers, app.py and the rule modules must not talk to the
database directly, and routers must not skip the service layer."""

import ast
import glob
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RULE_MODULES = ["app.py", "replenishment.py", "metrics.py", "forecast.py", "etl.py", "stockout.py"]
LAYERS = ["src/routers/*.py", "src/services/*.py", "src/parsers/*.py"]
DB_CALLS = {"text", "read_sql", "read_sql_query", "execute", "executemany", "execute_values", "copy_from",
            "create_engine"}


def _files():
    out = [p for p in RULE_MODULES if os.path.exists(os.path.join(ROOT, p))]
    for pat in LAYERS:
        out += sorted(glob.glob(pat, root_dir=ROOT))
    return out


def _db_calls(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
            if name in DB_CALLS:
                hits.append(f"{path}:{node.lineno} {name}()")
    return hits


def test_no_sql_outside_repositories():
    hits = [h for p in _files() for h in _db_calls(p)]
    assert not hits, "database calls outside src/repositories:\n" + "\n".join(hits)


def test_routers_only_call_services():
    for path in glob.glob("src/routers/*.py", root_dir=ROOT):
        with open(os.path.join(ROOT, path), encoding="utf-8") as fh:
            src = fh.read()
        assert "src.repositories" not in src and "sqlalchemy" not in src, path
