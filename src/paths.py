"""
Where the run keeps its state.

Seven files - the point budget, the search meter, the comparison cache, the
discovery ledger, the audit log, the run reports, the lock - each grew its own
absolute default of /opt/kdx/something, and each its own environment variable.
That is fine on the server and awkward everywhere else: running the pipeline
anywhere but /opt/kdx meant knowing all seven names, and getting one wrong meant
a run that wrote half its state into a directory nobody looks at.

So: one directory, one variable, and every individual override still honoured.

    KDX_STATE_DIR   where all of it lives      (default /opt/kdx)
    KDX_BUDGET_STATE, KDX_SEARCH_STATE, ...    still win where they are set

The default stays /opt/kdx so nothing already deployed moves.
"""

from __future__ import annotations

import os

DEFAULT_STATE_DIR = "/opt/kdx"


def state_dir() -> str:
    return os.environ.get("KDX_STATE_DIR", DEFAULT_STATE_DIR)


def state_path(filename: str, env_var: str = "") -> str:
    """
    The path for one piece of state: its own variable if set, else under the
    state directory. Nothing is created here - a module that only reads its
    state should not be making directories as a side effect of being imported.
    """
    if env_var:
        override = os.environ.get(env_var, "").strip()
        if override:
            return override
    return os.path.join(state_dir(), filename)
