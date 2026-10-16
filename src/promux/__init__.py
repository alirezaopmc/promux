"""Promux: Antigravity CLI profile multiplexer & quota failover daemon."""

import logging

logging.getLogger(__name__).addHandler(logging.NullHandler())


try:
    from ._version import __version__
except ImportError:
    __version__ = "1.0.0"
