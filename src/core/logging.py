"""Logging setup. Vercel captures stdout/stderr, so a plain stream handler is enough."""

import logging

_FORMAT = "%(levelname)s %(name)s: %(message)s"


def get_logger(name: str) -> logging.Logger:
    root = logging.getLogger("stockwise")
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        root.propagate = False
    return root.getChild(name)
