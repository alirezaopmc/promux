"""Promux: Antigravity CLI profile multiplexer & quota failover daemon."""

import logging

logging.getLogger(__name__).addHandler(logging.NullHandler())


def __getattr__(name: str) -> str:
    if name == "__version__":
        from .version import get_version

        return get_version()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
