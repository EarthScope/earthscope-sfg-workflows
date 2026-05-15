"""SQLite-backed :class:`AssetStore` adapter.
Defines the SQLAlchemy ORM tables (``Assets`` / ``MergeJobs``) that back the
on-disk asset catalog and exposes them through the :class:`AssetStore` port.

The asset catalog schema has no ``survey`` column; ``CampaignScope.survey``
is intentionally not persisted here. Survey-scoped metadata lives at the
workflow layer.
"""

from __future__ import annotations

import datetime as _dt
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    create_engine,
    delete,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import Engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker

from ..model import AssetEntry, AssetKind, SFGScope

Base = declarative_base()


class Assets(Base):
    """SQLAlchemy ORM model for the ``assets`` table."""

    __tablename__ = "assets"
    id = Column(Integer, primary_key=True, autoincrement=True)
    network = Column(String)
    station = Column(String)
    campaign = Column(String)
    remote_path = Column(String, nullable=True, unique=True)
    remote_type = Column(String, nullable=True)
    local_path = Column(String, nullable=True, unique=True)
    type = Column(String)
    timestamp_data_start = Column(DateTime, nullable=True)
    timestamp_data_end = Column(DateTime, nullable=True)
    timestamp_created = Column(DateTime, default=lambda: _dt.datetime.now(tz=_dt.UTC))
    parent_id = Column(Integer, ForeignKey("assets.id"), nullable=True)
    is_processed = Column(Boolean, default=False)


class MergeJobs(Base):
    """SQLAlchemy ORM model for the ``mergejobs`` table."""

    __tablename__ = "mergejobs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    child_type = Column(String)
    parent_ids = Column(String)
    parent_type = Column(String)


def _row_to_entry(row: Assets) -> AssetEntry:
    return AssetEntry(
        id=row.id,
        kind=AssetKind(row.type),
        scope=SFGScope(
            network=row.network or "",
            station=row.station or "",
            campaign=row.campaign,
        ),
        local_path=Path(row.local_path) if row.local_path else None,
        remote_path=row.remote_path,
        remote_type=row.remote_type,
        is_processed=bool(row.is_processed),
        parent_id=row.parent_id,
        timestamp_data_start=row.timestamp_data_start,
        timestamp_data_end=row.timestamp_data_end,
        timestamp_created=row.timestamp_created,
    )


def _entry_to_kwargs(asset: AssetEntry) -> dict:
    return {
        "network": asset.scope.network,
        "station": asset.scope.station,
        "campaign": asset.scope.campaign,
        "type": asset.kind.value,
        "local_path": str(asset.local_path) if asset.local_path else None,
        "remote_path": asset.remote_path,
        "remote_type": asset.remote_type,
        "is_processed": asset.is_processed,
        "parent_id": asset.parent_id,
        "timestamp_data_start": asset.timestamp_data_start,
        "timestamp_data_end": asset.timestamp_data_end,
        "timestamp_created": asset.timestamp_created or datetime.now(tz=timezone.utc),
    }


class AssetCatalog:
    """SQLAlchemy-backed asset catalog. Works with any SQLAlchemy URL.

    Backed by SQLite locally or Postgres/RDS in cloud. Pass an engine
    directly or use the factory classmethods :meth:`sqlite` and
    :meth:`from_url`.

    Attributes
    ----------
    _engine : Engine
        The underlying SQLAlchemy engine (not for direct use by callers).

    Methods
    -------
    sqlite(db_path, create_schema)
        Build a catalog backed by a local SQLite file.
    from_url(url, create_schema)
        Build a catalog from a SQLAlchemy database URL.
    add(asset)
        Insert an asset and return it with its assigned id.
    update(asset)
        Update an existing asset row by id.
    mark_processed_bulk(asset_ids)
        Mark multiple assets as processed in a single statement.
    by_id(asset_id)
        Look up an asset by primary key.
    by_local_path(path)
        Return all assets whose local_path matches a given path.
    assets_for(kind, network, station, campaign)
        Return assets matching the given scope fields.
    assets_to_process(kind, override, network, station, campaign)
        Return unprocessed assets optionally filtered by kind.
    delete(kind, network, station, campaign)
        Delete assets matching the given scope and optional kind.
    delete_by_id(asset_id)
        Delete a single asset by id.
    count_by_kind(network, station, campaign)
        Return per-kind row counts for the specified scope.
    distinct_values(field, **filters)
        Return sorted distinct non-null values of a scope field.
    add_merge_job(parent_type, child_type, parent_ids)
        Persist a record that a merge job ran.
    is_merge_complete(parent_type, child_type, parent_ids)
        Check whether a merge job has previously been recorded.
    close()
        Dispose of the underlying SQLAlchemy engine.
    """

    def __init__(self, engine: Engine, *, create_schema: bool = True) -> None:
        """Bind to an existing SQLAlchemy engine.

        Parameters
        ----------
        engine : Engine
            A pre-built SQLAlchemy :class:`~sqlalchemy.engine.Engine`.
        create_schema : bool, optional
            When ``True`` (default), run ``CREATE TABLE IF NOT EXISTS`` for
            all managed tables on first use.
        """
        self._engine = engine
        if create_schema:
            Base.metadata.create_all(self._engine)
        self._Session: sessionmaker[Session] = sessionmaker(
            bind=engine, expire_on_commit=False, future=True
        )

    # -- factories ---------------------------------------------------------

    @classmethod
    def sqlite(cls, db_path: Path, *, create_schema: bool = True) -> "AssetCatalog":
        """Build an :class:`AssetCatalog` backed by a local SQLite file.

        Parameters
        ----------
        db_path : Path
            Filesystem path to the SQLite database file. Parent directories
            are created automatically.
        create_schema : bool, optional
            When ``True`` (default), create tables if they do not exist.

        Returns
        -------
        AssetCatalog
            A catalog instance bound to the SQLite database at ``db_path``.
        """
        db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(f"sqlite:///{db_path}", future=True)
        return cls(engine, create_schema=create_schema)

    @classmethod
    def from_url(cls, url: str, *, create_schema: bool = True) -> "AssetCatalog":
        """Build an :class:`AssetCatalog` from a SQLAlchemy database URL.

        Parameters
        ----------
        url : str
            A SQLAlchemy connection URL (e.g. ``"sqlite:///path/to/db.sqlite"``
            or ``"postgresql://user:pass@host/db"``).
        create_schema : bool, optional
            When ``True`` (default), create tables if they do not exist.

        Returns
        -------
        AssetCatalog
            A catalog instance bound to the given database URL.
        """
        return cls(create_engine(url, future=True), create_schema=create_schema)

    # -- AssetStore protocol ----------------------------------------------

    def add(self, asset: AssetEntry) -> AssetEntry:
        """Insert an asset and return it with its assigned id.

        Parameters
        ----------
        asset : AssetEntry
            The asset to persist. ``asset.id`` is ignored; a new id is assigned.

        Returns
        -------
        AssetEntry
            A copy of ``asset`` with ``id`` populated from the database.

        Raises
        ------
        IntegrityError
            If ``local_path`` or ``remote_path`` already exists (UNIQUE
            constraint violation). Callers may treat this as a no-op skip;
            the existing matching entry is returned instead.
        """
        try:
            with self._Session.begin() as session:
                row = Assets(**_entry_to_kwargs(asset))
                session.add(row)
                session.flush()
                return _row_to_entry(row)
        except IntegrityError:
            # Duplicate local_path or remote_path — return existing entry.
            existing = self.by_local_path(Path(asset.local_path)) if asset.local_path else []
            if existing:
                return existing[0]
            raise

    def update(self, asset: AssetEntry) -> bool:
        """Update an existing asset row by id.

        Parameters
        ----------
        asset : AssetEntry
            Asset containing updated field values. Must have ``id`` set.

        Returns
        -------
        bool
            ``True`` if a row was modified; ``False`` if the id was ``None``
            or no matching row existed.
        """
        if asset.id is None:
            return False
        with self._Session.begin() as session:
            stmt = update(Assets).where(Assets.id == asset.id).values(**_entry_to_kwargs(asset))
            result = session.execute(stmt)
            return result.rowcount > 0

    def mark_processed_bulk(self, asset_ids: list[int]) -> int:
        """Mark multiple assets as processed in a single statement.

        Parameters
        ----------
        asset_ids : list[int]
            Primary keys of assets to mark as processed.

        Returns
        -------
        int
            Number of rows updated.
        """
        if not asset_ids:
            return 0
        with self._Session.begin() as session:
            stmt = update(Assets).where(Assets.id.in_(asset_ids)).values(is_processed=True)
            result = session.execute(stmt)
            return result.rowcount

    def by_id(self, asset_id: int) -> AssetEntry | None:
        """Look up an asset by primary key.

        Parameters
        ----------
        asset_id : int
            The primary key to look up.

        Returns
        -------
        AssetEntry or None
            The matching entry, or ``None`` if no row has that id.
        """
        with self._Session() as session:
            row = session.get(Assets, asset_id)
            return None if row is None else _row_to_entry(row)

    def by_local_path(self, path: Path) -> list[AssetEntry]:
        """Return all assets whose local_path matches a given path.

        Parameters
        ----------
        path : Path
            Filesystem path to match against.

        Returns
        -------
        list[AssetEntry]
            All entries with ``local_path == str(path)``.
        """
        with self._Session() as session:
            rows = (
                session.execute(select(Assets).where(Assets.local_path == str(path)))
                .scalars()
                .all()
            )
            return [_row_to_entry(r) for r in rows]

    def assets_for(
        self,
        kind: AssetKind | None = None,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
    ) -> list[AssetEntry]:
        """Return assets matching the given scope fields.

        All scope fields default to ``None``; ``None`` is treated as an exact
        ``NULL`` match rather than a wildcard.  To query across all campaigns
        for a station, use :meth:`distinct_values` first.

        Parameters
        ----------
        kind : AssetKind or None, optional
            Filter to a specific asset kind. ``None`` returns all kinds.
        network : str or None, optional
            Network to filter on (exact match).
        station : str or None, optional
            Station to filter on (exact match).
        campaign : str or None, optional
            Campaign to filter on (exact match).

        Returns
        -------
        list[AssetEntry]
            All matching entries, ordered by id.
        """
        stmt = select(Assets).where(
            Assets.network == network,
            Assets.station == station,
            Assets.campaign == campaign,
        )
        if kind is not None:
            stmt = stmt.where(Assets.type == kind.value)
        with self._Session() as session:
            rows = session.execute(stmt.order_by(Assets.id)).scalars().all()
            return [_row_to_entry(r) for r in rows]

    def assets_to_process(
        self,
        kind: AssetKind | None = None,
        override: bool = False,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
    ) -> list[AssetEntry]:
        """Return unprocessed assets optionally filtered by kind.

        Parameters
        ----------
        kind : AssetKind or None, optional
            Filter to a specific asset kind. ``None`` returns all kinds.
        override : bool, optional
            When ``True``, returns all matching assets regardless of processed
            status (equivalent to calling :meth:`assets_for`).
        network : str or None, optional
            Network to filter on.
        station : str or None, optional
            Station to filter on.
        campaign : str or None, optional
            Campaign to filter on.

        Returns
        -------
        list[AssetEntry]
            Unprocessed (or all, when ``override=True``) matching entries.
        """
        if override:
            return self.assets_for(network=network, station=station, campaign=campaign, kind=kind)

        stmt = select(Assets).where(
            Assets.network == network,
            Assets.station == station,
            Assets.campaign == campaign,
            Assets.is_processed.is_(False),
        )
        if kind is not None:
            stmt = stmt.where(Assets.type == kind.value)
        with self._Session() as session:
            rows = session.execute(stmt.order_by(Assets.id)).scalars().all()
            return [_row_to_entry(r) for r in rows]

    def delete(
        self,
        kind: AssetKind | None = None,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
    ) -> int:
        """Delete assets matching the given scope fields and optional kind.

        Parameters
        ----------
        kind : AssetKind or None, optional
            Restrict deletion to this asset kind. ``None`` deletes all kinds
            in the given scope.
        network : str or None, optional
            Network to match.
        station : str or None, optional
            Station to match.
        campaign : str or None, optional
            Campaign to match.

        Returns
        -------
        int
            Number of rows deleted.
        """
        stmt = delete(Assets).where(
            Assets.network == network,
            Assets.station == station,
            Assets.campaign == campaign,
        )
        if kind is not None:
            stmt = stmt.where(Assets.type == kind.value)
        with self._Session.begin() as session:
            return session.execute(stmt).rowcount or 0

    def delete_by_id(self, asset_id: int) -> bool:
        """Delete a single asset by primary key.

        Parameters
        ----------
        asset_id : int
            Primary key of the asset to delete.

        Returns
        -------
        bool
            ``True`` if a row was deleted; ``False`` if no matching row existed.
        """
        with self._Session.begin() as session:
            result = session.execute(delete(Assets).where(Assets.id == asset_id))
            return (result.rowcount or 0) > 0

    def count_by_kind(
        self,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
    ) -> dict[AssetKind, int]:
        """Return per-kind row counts for assets in the specified scope.

        Parameters
        ----------
        network : str or None, optional
            Network to filter on.
        station : str or None, optional
            Station to filter on.
        campaign : str or None, optional
            Campaign to filter on.

        Returns
        -------
        dict[AssetKind, int]
            Mapping of :class:`AssetKind` to row count. Only kinds with at
            least one row are included. Unknown legacy kind values are skipped.
        """
        with self._Session() as session:
            rows = (
                session.execute(
                    select(Assets.type).where(
                        Assets.network == network,
                        Assets.station == station,
                        Assets.campaign == campaign,
                    )
                )
                .scalars()
                .all()
            )
        counts: dict[AssetKind, int] = defaultdict(int)
        for t in rows:
            try:
                counts[AssetKind(t)] += 1
            except ValueError:
                # Unknown legacy type values — skip rather than crash callers.
                continue
        return dict(counts)

    def distinct_values(self, field: str, **filters: str | None) -> list[str]:
        """Return sorted distinct non-null values of a scope field.

        Parameters
        ----------
        field : str
            Column to query. Must be one of ``"network"``, ``"station"``,
            or ``"campaign"``.
        **filters : str or None
            Optional equality filters using the same supported column names.

        Returns
        -------
        list[str]
            Sorted list of distinct non-null values.

        Raises
        ------
        ValueError
            If ``field`` or any filter key is not a supported column name.
        """
        _col_map = {
            "network": Assets.network,
            "station": Assets.station,
            "campaign": Assets.campaign,
        }
        if field not in _col_map:
            raise ValueError(f"Unsupported field for distinct_values: {field!r}")
        col = _col_map[field]
        stmt = select(col).where(col.isnot(None))
        for k, v in filters.items():
            if k not in _col_map:
                raise ValueError(f"Unsupported filter key: {k!r}")
            if v is not None:
                stmt = stmt.where(_col_map[k] == v)
        stmt = stmt.distinct()
        with self._Session() as session:
            rows = session.execute(stmt).scalars().all()
        return sorted(r for r in rows if r)

    def close(self) -> None:
        """Dispose of the underlying SQLAlchemy engine, releasing all connections."""
        self._engine.dispose()

    # -- merge job tracking ----------------------------------------------

    @staticmethod
    def _merge_signature(parent_ids: list[int] | list[str]) -> str:
        ids = sorted(str(x) for x in parent_ids)
        return "-".join(ids)

    def add_merge_job(
        self,
        parent_type: str,
        child_type: str,
        parent_ids: list[int] | list[str],
    ) -> None:
        """Persist a record that a merge job ran.

        Parameters
        ----------
        parent_type : str
            Asset kind string for the parent (input) assets.
        child_type : str
            Asset kind string for the child (output) asset.
        parent_ids : list[int] or list[str]
            Ids of the parent assets consumed in this merge.
        """
        sig = self._merge_signature(parent_ids)
        with self._Session.begin() as session:
            session.add(
                MergeJobs(
                    parent_type=parent_type,
                    child_type=child_type,
                    parent_ids=sig,
                )
            )

    def is_merge_complete(
        self,
        parent_type: str,
        child_type: str,
        parent_ids: list[int] | list[str],
    ) -> bool:
        """Check whether a merge job for these inputs has previously been recorded.

        Parameters
        ----------
        parent_type : str
            Asset kind string for the parent assets.
        child_type : str
            Asset kind string for the child asset.
        parent_ids : list[int] or list[str]
            Ids of the parent assets to check.

        Returns
        -------
        bool
            ``True`` if a matching merge record exists; ``False`` otherwise.
        """
        sig = self._merge_signature(parent_ids)
        with self._Session() as session:
            row = session.execute(
                select(MergeJobs).where(
                    MergeJobs.parent_type == parent_type,
                    MergeJobs.child_type == child_type,
                    MergeJobs.parent_ids == sig,
                )
            ).first()
            return row is not None


__all__ = ["AssetCatalog"]
