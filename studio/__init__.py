"""Studio - a local web UI for the shorts-factory batch flow.

Holds no batch state of its own: everything is derived from projects/<id>/ and every change
goes through lib/shorts and scripts/shorts_batch.py.
"""

__version__ = "0.1.0"

DEFAULT_PORT = 4760
