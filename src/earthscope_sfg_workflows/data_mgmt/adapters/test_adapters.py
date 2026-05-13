"""Test adapters for the data_mgmt ports.

Re-exports in-memory doubles so tests only need a single import location.

Typical usage::

    from earthscope_sfg_workflows.data_mgmt.adapters.test_adapters import (
        FakeArchive,
        InMemoryAssetStore,
        InMemoryFileStore,
    )
"""

from earthscope_sfg_workflows.data_mgmt.adapters.memory import (
    FakeArchive,
    InMemoryAssetStore,
    InMemoryFileStore,
)

__all__ = [
    "FakeArchive",
    "InMemoryAssetStore",
    "InMemoryFileStore",
]
