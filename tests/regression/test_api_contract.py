"""API contract: every path/method of the reference commit still exists, with the same parameter names,
defaults and body/file inputs. Reads the reference app.py with `git show` (no database needed)."""

import ast
import inspect
import os
import subprocess
import textwrap

import pytest

from tests.conftest import ROOT

BASE_REF = os.getenv("REGRESSION_BASE_REF", "e6e1e5c")
# Removed on purpose, with approval (decision U6, 2026-11-03): unused by the dashboard.
APPROVED_REMOVALS = {("GET", "/api/coverage")}


def _params(fn):
    """{name: default source text or None} — same representation for reference and current code."""
    args = fn.args.args
    dflts = [None] * (len(args) - len(fn.args.defaults)) + list(fn.args.defaults)
    return {a.arg: (ast.unparse(d).replace('"', "'") if d is not None else None) for a, d in zip(args, dflts)}


def _reference_routes():
    try:
        src = subprocess.run(["git", "-C", ROOT, "show", f"{BASE_REF}:app.py"], capture_output=True, text=True,
                             check=True).stdout
    except Exception:
        pytest.skip("git history with the reference commit not available")
    out = {}
    for n in ast.parse(src).body:
        for d in getattr(n, "decorator_list", []):
            if isinstance(d, ast.Call) and getattr(d.func, "attr", None) in ("get", "post", "patch", "delete", "put"):
                out[(d.func.attr.upper(), d.args[0].value)] = _params(n)
    return out


def _current_routes():
    import app as A
    from fastapi.routing import APIRoute
    def flat(routes):
        for r in routes:
            inner = getattr(r, "original_router", None)          # FastAPI >= 0.16x wraps included routers
            if inner is not None:
                yield from flat(inner.routes)
            else:
                yield r

    out = {}
    for r in flat(A.app.routes):
        if not isinstance(r, APIRoute):
            continue
        fn = ast.parse(textwrap.dedent(inspect.getsource(r.endpoint))).body[0]
        params = _params(fn)
        for m in r.methods:
            out[(m, r.path)] = params
    return out


def test_every_reference_route_kept_with_same_parameters():
    ref, cur = _reference_routes(), _current_routes()
    assert not (APPROVED_REMOVALS & set(cur)), "an approved removal is still registered"
    missing = sorted(set(ref) - set(cur) - APPROVED_REMOVALS)
    assert not missing, f"routes removed: {missing}"
    diffs = []
    for key, params in ref.items():
        if key in APPROVED_REMOVALS:
            continue
        now = cur[key]
        if list(params) != list(now):
            diffs.append(f"{key}: params {list(params)} -> {list(now)}")
            continue
        for p, d in params.items():
            if d != now[p]:
                diffs.append(f"{key}: default of {p} {d!r} -> {now[p]!r}")
    assert not diffs, "\n".join(diffs)


def test_auth_routes_present():
    cur = _current_routes()
    for key in [("POST", "/api/auth/login"), ("POST", "/api/auth/logout"), ("GET", "/api/auth/me")]:
        assert key in cur
