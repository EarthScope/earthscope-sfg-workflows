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
from sqlalchemy.engine import Engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker

from ..model import AssetEntry, AssetKind, CampaignScope

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
    local_path = Column(String, nullable=True, unique=False)
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
        scope=CampaignScope(
            network=row.network,
            station=row.station,
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


class SqlAssetStore:
    """SQLAlchemy-backed :class:`AssetStore`. Works with any URL — SQLite locally,
    Postgres/RDS in cloud. Pass an engine directly or use the factory classmethods.
    """

    def __init__(self, engine: Engine, *, create_schema: bool = True) -> None:
        self._engine = engine
        if create_schema:
            Base.metadata.create_all(self._engine)
        self._Session: sessionmaker[Session] = sessionmaker(
            bind=engine, expire_on_commit=False, future=True
        )

    # -- factories ---------------------------------------------------------

    @classmethod
    def sqlite(cls, db_path: Path, *, create_schema: bool = True) -> "SqlAssetStore":
        db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(f"sqlite:///{db_path}", future=True)
        return cls(engine, create_schema=create_schema)

    @classmethod
    def from_url(cls, url: str, *, create_schema: bool = True) -> "SqlAssetStore":
        return cls(create_engine(url, future=True), create_schema=create_schema)

    # -- AssetStore protocol ----------------------------------------------

    def add(self, asset: AssetEntry) -> AssetEntry:
        with self._Session.begin() as session:
            row = Assets(**_entry_to_kwargs(asset))
            session.add(row)
            session.flush()
            return _row_to_entry(row)

    def update(self, asset: AssetEntry) -> bool:
        if asset.id is None:
            return False
        with self._Session.begin() as session:
            stmt = (
                update(Assets)
                .where(Assets.id == asset.id)
                .values(**_entry_to_kwargs(asset))
            )
            result = session.execute(stmt)
            return result.rowcount > 0

    def by_id(self, asset_id: int) -> AssetEntry | None:
        with self._Session() as session:
            row = session.get(Assets, asset_id)
            return None if row is None else _row_to_entry(row)

    def by_local_path(self, path: Path) -> list[AssetEntry]:
        with self._Session() as session:
            rows = session.execute(
                select(Assets).where(Assets.local_path == str(path))
            ).scalars().all()
            return [_row_to_entry(r) for r in rows]

    def assets_for(
        self,
        scope: CampaignScope,
        kind: AssetKind | None = None,
    ) -> list[AssetEntry]:
        stmt = select(Assets).where(
            Assets.network == scope.network,
            Assets.station == scope.station,
            Assets.campaign == scope.campaign,
        )
        if kind is not None:
            stmt = stmt.where(Assets.type == kind.value)
        with self._Session() as session:
            rows = session.execute(stmt.order_by(Assets.id)).scalars().all()
            return [_row_to_entry(r) for r in rows]

    def delete(
        self,
        scope: CampaignScope,
        kind: AssetKind | None = None,
    ) -> int:
        stmt = delete(Assets).where(
            Assets.network == scope.network,
            Assets.station == scope.station,
            Assets.campaign == scope.campaign,
        )
        if kind is not None:
            stmt = stmt.where(Assets.type == kind.value)
        with self._Session.begin() as session:
            return session.execute(stmt).rowcount or 0

    def delete_by_id(self, asset_id: int) -> bool:
        with self._Session.begin() as session:
            result = session.execute(delete(Assets).where(Assets.id == asset_id))
            return (result.rowcount or 0) > 0

    def count_by_kind(self, scope: CampaignScope) -> dict[AssetKind, int]:
        with self._Session() as session:
            rows = session.execute(
                select(Assets.type).where(
                    Assets.network == scope.network,
                    Assets.station == scope.station,
                    Assets.campaign == scope.campaign,
                )
            ).scalars().all()
        counts: dict[AssetKind, int] = defaultdict(int)
        for t in rows:
            try:
                counts[AssetKind(t)] += 1
            except ValueError:
                # Unknown legacy type values — skip rather than crash callers.
                continue
        return dict(counts)

    def close(self) -> None:
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


__all__ = ["SqlAssetStore"]
