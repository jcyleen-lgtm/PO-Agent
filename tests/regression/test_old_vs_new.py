"""Regression: the same scenario run on the reference commit and on the working tree must give the
same HTTP status codes and the same JSON (timestamps masked).

    TEST_DATABASE_URL=... REGRESSION_BASE_REF=e6e1e5c pytest tests/regression -q

The reference tree is checked out with `git worktree` into a temp folder. Both runs reset the same
test database, one after the other, on the same day (dates like 'order by in N days' depend on today).
"""

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

from tests.conftest import ROOT, TEST_DB

BASE_REF = os.getenv("REGRESSION_BASE_REF", "e6e1e5c")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="TEST_DATABASE_URL not set")
SCENARIO = os.path.join(ROOT, "tests", "regression", "scenario.py")


def run(code_dir, out):
    env = dict(os.environ, TEST_DATABASE_URL=TEST_DB, PYTHONDONTWRITEBYTECODE="1")
    # The reference commit ranks Pareto over all history; the current code over the last 26 weeks (decision of
    # 5 Nov 2026). With a window longer than the data, both must produce the same results.
    env.setdefault("SW_PARETO_WINDOW_WEEKS", "5000")
    for k in ("APP_PASSWORD_HASH",):
        env.pop(k, None)
    p = subprocess.run([sys.executable, SCENARIO, "--code", code_dir, "--out", out], env=env,
                       capture_output=True, text=True, timeout=1800)
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-4000:]
    with open(out, encoding="utf-8") as f:
        return json.load(f)


def diff(a, b, path="$", out=None, limit=40):
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        if list(a.keys()) != list(b.keys()):
            out.append(f"{path}: keys {sorted(set(a) ^ set(b)) or 'order differs'}")
        for k in a:
            if k in b:
                diff(a[k], b[k], f"{path}.{k}", out, limit)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} != {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f"{path}[{i}]", out, limit)
    elif isinstance(a, float) and isinstance(b, (int, float)) or isinstance(b, float) and isinstance(a, (int, float)):
        if not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9):
            out.append(f"{path}: {a!r} != {b!r}")
    elif a != b:
        out.append(f"{path}: {str(a)[:80]!r} != {str(b)[:80]!r}")
    return out


# Fields added after the reference commit (Pareto window info); their presence is not a difference.
_ADDED = {"pareto_window_start", "pareto_window_weeks", "window_weeks"}


def NEW_KEYS_ONLY(d):
    if ": keys [" not in d:
        return False
    keys = {k.strip(" '") for k in d.split(": keys [", 1)[1].rstrip("]").split(",")}
    return keys <= _ADDED


@pytest.fixture(scope="module")
def base_tree():
    tmp = tempfile.mkdtemp(prefix="sw_base_")
    wt = os.path.join(tmp, "base")
    subprocess.run(["git", "-C", ROOT, "worktree", "add", "--detach", wt, BASE_REF], check=True, capture_output=True)
    yield wt
    subprocess.run(["git", "-C", ROOT, "worktree", "remove", "--force", wt], capture_output=True)
    shutil.rmtree(tmp, ignore_errors=True)


def test_old_vs_new(base_tree, tmp_path):
    old = run(base_tree, str(tmp_path / "old.json"))
    new = run(ROOT, str(tmp_path / "new.json"))
    assert [s["step"] for s in old] == [s["step"] for s in new]
    problems = []
    for o, n in zip(old, new):
        if o["status"] != n["status"]:
            problems.append(f"{o['step']}: status {o['status']} != {n['status']}")
        problems += [f"{o['step']}: {d}" for d in diff(o["body"], n["body"]) if not NEW_KEYS_ONLY(d)]
    assert not problems, f"{len(problems)} differences:\n" + "\n".join(problems[:60])
    assert len(old) > 150
