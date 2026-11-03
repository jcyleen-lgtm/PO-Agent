"""Frontend regression run: seed a local DB, prepare data, start the app, drive every page in a browser.

    TEST_DATABASE_URL=postgresql://postgres@localhost:5432/stockwise_test \
        python tests/e2e/run_regression.py --out /tmp/fe-before
    python tests/e2e/compare.py /tmp/fe-before /tmp/fe-after

Needs Node with `playwright` and `chart.js` (see tests/e2e/package.json; `npm install` in tests/e2e).
Never point it at Supabase: the database is reset.
"""

import argparse
import datetime as dt
import importlib.util
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PASSWORD = "rahasia123"


def load_seed():
    spec = importlib.util.spec_from_file_location("sw_seed", os.path.join(ROOT, "tests", "fixtures", "seed.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def wait_port(port, timeout=40):
    t0 = time.time()
    while time.time() - t0 < timeout:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.3)
    raise SystemExit("server did not start")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", type=int, default=8796)
    ap.add_argument("--root", default=ROOT, help="code tree to test (default: this checkout)")
    a = ap.parse_args()
    db = os.environ["TEST_DATABASE_URL"]
    if "supabase" in db or "pooler" in db:
        raise SystemExit("refusing to run against Supabase")
    os.makedirs(a.out, exist_ok=True)
    fx = os.path.join(a.out, "_fixtures")
    os.makedirs(fx, exist_ok=True)

    code = os.path.abspath(a.root)
    os.chdir(code)
    sys.path.insert(0, code)
    from auth import hash_password
    env = dict(os.environ, SUPABASE_DB_URL=db, APP_USERNAME="admin", SESSION_SECRET="x" * 40,
               APP_PASSWORD_HASH=hash_password(PASSWORD))
    os.environ.update(env)

    S = load_seed()
    import app as A
    from fastapi.testclient import TestClient
    skus = S.seed(A.get_engine())
    c = TestClient(A.app)
    assert c.post("/api/auth/login", json={"username": "admin", "password": PASSWORD}).status_code == 200
    # Same preparation every run: forecast, PO master list, Hero list, a stock snapshot.
    assert c.post("/api/forecast/run").status_code == 200
    assert c.post("/api/po/upload", files={"file": ("LIST_PO.xlsx", S.po_master_file(skus), "x")}).status_code == 200
    from sqlalchemy import text
    with A.get_engine().connect() as k:
        fc = {r[0] for r in k.execute(text("SELECT sku FROM forecast_results"))}
    assert c.post("/api/hero-sku/upload", files={"file": ("HERO.xlsx", S.hero_file(skus, fc), "x")}).status_code == 200
    assert c.post("/api/forecast/run").status_code == 200
    stock = [[s, "x", "1.234,00"] for s in skus[:40]]
    today = dt.date.today().isoformat()
    assert c.post("/api/stock/upload", files={"file": ("stok.xlsx", S.stock_file(stock), "x")},
                  data={"report_date": today}).status_code == 200
    # Files the browser uploads during the run
    with open(os.path.join(fx, "HERO.xlsx"), "wb") as fh:
        fh.write(S.hero_file(skus, fc).getvalue())
    with open(os.path.join(fx, "bad.txt"), "wb") as fh:
        fh.write(b"not a spreadsheet")

    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--port", str(a.port), "--log-level", "warning"],
                           cwd=code, env=env, stdout=open(os.path.join(a.out, "_server.log"), "w"), stderr=subprocess.STDOUT)
    try:
        wait_port(a.port)
        r = subprocess.run(["node", os.path.join(HERE, "regression.js"), f"http://127.0.0.1:{a.port}", a.out, fx],
                           cwd=HERE)
        if r.returncode:
            raise SystemExit(r.returncode)
    finally:
        srv.terminate()
        srv.wait(10)
    log = open(os.path.join(a.out, "_server.log")).read()
    if "Traceback" in log:
        print("server log has tracebacks — see _server.log")


if __name__ == "__main__":
    main()
