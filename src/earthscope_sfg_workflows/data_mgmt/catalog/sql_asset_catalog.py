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
    """SQLAlchemy-backed :class:`AssetStore`. Works with any URL — SQLite locally,
    Postgres/RDS in cloud. Pass an engine directly or use the factory classmethods.
    """

    def __init__(self, engine: Engine, *, create_schema: bool = True) -> None:
        """Bind to a SQLAlchemy `Engine`; create tables when `create_schema`."""
        self._engine = engine
        if create_schema:
            Base.metadata.create_all(self._engine)
        self._Session: sessionmaker[Session] = sessionmaker(
            bind=engine, expire_on_commit=False, future=True
        )



    # -- factories ---------------------------------------------------------

    @classmethod
    def sqlite(cls, db_path: Path, *, create_schema: bool = True) -> "AssetCatalog":
        """Build a `AssetCatalog` backed by a local SQLite file at `db_path`."""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(f"sqlite:///{db_path}", future=True)
        return cls(engine, create_schema=create_schema)

    @classmethod
    def from_url(cls, url: str, *, create_schema: bool = True) -> "AssetCatalog":
        """Build a `AssetCatalog` from a SQLAlchemy database URL."""
        return cls(create_engine(url, future=True), create_schema=create_schema)

    # -- AssetStore protocol ----------------------------------------------

    def add(self, asset: AssetEntry) -> AssetEntry:
        """Insert `asset`, returning the persisted entry with its assigned id.

        Raises ``IntegrityError`` if the local_path or remote_path already
        exists (UNIQUE constraint violation), which callers can treat as a
        no-op skip.
        """
        try:
            with self._Session.begin() as session:
                row = Assets(**_entry_to_kwargs(asset))
                session.add(row)
                session.flush()
                return _row_to_entry(row)
        except IntegrityError:
            # Duplicate local_path or remote_path — return existing entry.
            existing = (
                self.by_local_path(Path(asset.local_path)) if asset.local_path else []
            )
            if existing:
                return existing[0]
            raise

    def update(self, asset: AssetEntry) -> bool:
        """Update an existing row by id; return True iff a row was modified."""
        if asset.id is None:
            return False
        with self._Session.begin() as session:
            stmt = update(Assets).where(Assets.id == asset.id).values(**_entry_to_kwargs(asset))
            result = session.execute(stmt)
            return result.rowcount > 0

    def mark_processed_bulk(self, asset_ids: list[int]) -> int:
        """Mark multiple assets as processed by id. Returns count of updated rows."""
        if not asset_ids:
            return 0
        with self._Session.begin() as session:
            stmt = update(Assets).where(Assets.id.in_(asset_ids)).values(is_processed=True)
            result = session.execute(stmt)
            return result.rowcount

    def by_id(self, asset_id: int) -> AssetEntry | None:
        """Return the asset with `asset_id`, or None if absent."""
        with self._Session() as session:
            row = session.get(Assets, asset_id)
            return None if row is None else _row_to_entry(row)

    def by_local_path(self, path: Path) -> list[AssetEntry]:
        """Return all assets whose `local_path` matches `path`."""
        with self._Session() as session:
            rows = (
                session.execute(select(Assets).where(Assets.local_path == str(path)))
                .scalars()
                .all()
            )
            return [_row_to_entry(r) for r in rows]

    def assets_for(
        self,
        scope: "SFGScope | None" = None,
        kind: AssetKind | None = None,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
    ) -> list[AssetEntry]:
        """Return assets in ``scope``, optionally filtered by ``kind``, ordered by id."""
        net = scope.network if scope is not None else network
        sta = scope.station if scope is not None else station
        camp = scope.campaign if scope is not None else campaign
        stmt = select(Assets).where(
            Assets.network == net,
            Assets.station == sta,
            Assets.campaign == camp,
        )
        if kind is not None:
            stmt = stmt.where(Assets.type == kind.value)
        with self._Session() as session:
            rows = session.execute(stmt.order_by(Assets.id)).scalars().all()
            return [_row_to_entry(r) for r in rows]

    def assets_to_process(
        self,
        scope: "SFGScope | None" = None,
        kind: AssetKind | None = None,
        override: bool = False,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
    ) -> list[AssetEntry]:
        """Return unprocessed assets in ``scope`` filtered by ``kind``."""
        net = scope.network if scope is not None else network
        sta = scope.station if scope is not None else station
        camp = scope.campaign if scope is not None else campaign
        if override:
            return self.assets_for(network=net, station=sta, campaign=camp, kind=kind)

        stmt = select(Assets).where(
            Assets.network == net,
            Assets.station == sta,
            Assets.campaign == camp,
            Assets.is_processed.is_(False),
        )
        if kind is not None:
            stmt = stmt.where(Assets.type == kind.value)
        with self._Session() as session:
            rows = session.execute(stmt.order_by(Assets.id)).scalars().all()
            return [_row_to_entry(r) for r in rows]

    def delete(
        self,
        scope: "SFGScope | None" = None,
        kind: AssetKind | None = None,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
    ) -> int:
        """Delete assets matching the scope (and optional kind). Return count."""
        net = scope.network if scope is not None else network
        sta = scope.station if scope is not None else station
        camp = scope.campaign if scope is not None else campaign
        stmt = delete(Assets).where(
            Assets.network == net,
            Assets.station == sta,
            Assets.campaign == camp,
        )
        if kind is not None:
            stmt = stmt.where(Assets.type == kind.value)
        with self._Session.begin() as session:
            return session.execute(stmt).rowcount or 0

    def delete_by_id(self, asset_id: int) -> bool:
        """Delete a single asset by id; return True iff a row was deleted."""
        with self._Session.begin() as session:
            result = session.execute(delete(Assets).where(Assets.id == asset_id))
            return (result.rowcount or 0) > 0

    def count_by_kind(
        self,
        scope: "SFGScope | None" = None,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
    ) -> dict[AssetKind, int]:
        """Return a per-`AssetKind` row count for assets in the specified scope."""
        net = scope.network if scope is not None else network
        sta = scope.station if scope is not None else station
        camp = scope.campaign if scope is not None else campaign
        with self._Session() as session:
            rows = (
                session.execute(
                    select(Assets.type).where(
                        Assets.network == net,
                        Assets.station == sta,
                        Assets.campaign == camp,
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
        """Return sorted distinct non-null values of *field* matching *filters*."""
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
        """Dispose of the underlying SQLAlchemy `Engine`."""
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
        """Persist a record that a merge from `parent_ids` produced a `child_type`."""
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
        """Return True iff a matching merge job has previously been recorded."""
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
