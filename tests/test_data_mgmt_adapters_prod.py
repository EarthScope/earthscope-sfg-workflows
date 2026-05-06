"""Wiring / shape tests for production adapters.

These don't hit AWS or the EarthScope archive — they just verify the
adapters are importable, expose the port surface, and translate errors as
documented. Real network roundtrips belong in integration tests gated on
credentials.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from earthscope_sfg_workflows.data_mgmt import (
    ArchiveAuthError,
    ArchiveError,
    ArchiveNotFoundError,
    ArchiveSource,
    FileStore,
)


# ---------------------------------------------------------------------------
# EarthScopeArchive
# ---------------------------------------------------------------------------


class TestEarthScopeArchiveShape:
    def test_implements_archive_source_protocol(self) -> None:
        from earthscope_sfg_workflows.data_mgmt.adapters import EarthScopeArchive

        arc = EarthScopeArchive()
        assert isinstance(arc, ArchiveSource)

    def test_list_files_translates_404(self) -> None:
        from earthscope_sfg_workflows.data_mgmt.adapters import EarthScopeArchive
        import urllib.error

        arc = EarthScopeArchive()
        arc._token = "fake"  # bypass auth path

        def boom(req: Any) -> Any:
            raise urllib.error.HTTPError(url=req.full_url, code=404, msg="nf", hdrs=None, fp=None)

        with patch("urllib.request.urlopen", side_effect=boom):
            with pytest.raises(ArchiveNotFoundError):
                arc.list_files("https://archive/missing")

    def test_list_files_translates_401(self) -> None:
        from earthscope_sfg_workflows.data_mgmt.adapters import EarthScopeArchive
        import urllib.error

        arc = EarthScopeArchive()
        arc._token = "fake"

        def boom(req: Any) -> Any:
            raise urllib.error.HTTPError(url=req.full_url, code=401, msg="auth", hdrs=None, fp=None)

        with patch("urllib.request.urlopen", side_effect=boom):
            with pytest.raises(ArchiveAuthError):
                arc.list_files("https://archive/x")

    def test_download_translates_status(self, tmp_path: Path) -> None:
        from earthscope_sfg_workflows.data_mgmt.adapters import EarthScopeArchive

        arc = EarthScopeArchive()
        arc._token = "fake"

        resp = MagicMock(status_code=500, reason="boom")
        with patch("requests.get", return_value=resp):
            with pytest.raises(ArchiveError):
                arc.download_file("https://archive/x", tmp_path / "x")


# ---------------------------------------------------------------------------
# S3FileStore
# ---------------------------------------------------------------------------


class TestS3FileStoreShape:
    def test_implements_file_store_protocol(self) -> None:
        from earthscope_sfg_workflows.data_mgmt.adapters import S3FileStore

        fs = S3FileStore()
        assert isinstance(fs, FileStore)

    def test_local_paths_pass_through(self, tmp_path: Path) -> None:
        from earthscope_sfg_workflows.data_mgmt.adapters import S3FileStore

        fs = S3FileStore()
        target = tmp_path / "sub" / "x.bin"
        fs.write_bytes(target, b"hello")
        assert fs.is_file(target)
        assert fs.read_bytes(target) == b"hello"
        assert fs.get_size(target) == 5

    def test_mkdir_on_s3_path_is_noop(self) -> None:
        from earthscope_sfg_workflows.data_mgmt.adapters import S3FileStore

        # Should not raise even though no S3 client is configured: we never
        # actually contact S3 here.
        fs = S3FileStore()
        fs.mkdir(Path("s3://bucket/some/prefix"))
