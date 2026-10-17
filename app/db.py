"""SQLAlchemy models + session. SQLite by default (DATABASE_URL=sqlite:///./studio.db), Postgres via DATABASE_URL."""
import datetime as dt
import json
import os

from sqlalchemy import Float, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./studio.db")
for old, new in (("postgres://", "postgresql+psycopg://"), ("postgresql://", "postgresql+psycopg://")):
    if DATABASE_URL.startswith(old):
        DATABASE_URL = new + DATABASE_URL[len(old):]
IS_SQLITE = DATABASE_URL.startswith("sqlite")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if IS_SQLITE else {}, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class JsonData:
    """Mixin: `data` property backed by a JSON text column."""
    data_json: Mapped[str]

    @property
    def data(self) -> dict:
        return json.loads(self.data_json or "{}")

    @data.setter
    def data(self, value: dict):
        self.data_json = json.dumps(value, ensure_ascii=False)


class Setting(Base, JsonData):
    __tablename__ = "settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    data_json: Mapped[str] = mapped_column(Text, default="{}")


class Flow(Base, JsonData):
    __tablename__ = "flows"
    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(32))  # kit | publish | weekly
    enabled: Mapped[int] = mapped_column(Integer, default=1)
    data_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    flow_slug: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | running | done | failed
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    log_json: Mapped[str] = mapped_column(Text, default="[]")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    kit_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)

    @property
    def params(self) -> dict:
        return json.loads(self.params_json or "{}")

    @property
    def log(self) -> list:
        return json.loads(self.log_json or "[]")


class Kit(Base, JsonData):
    __tablename__ = "kits"
    date: Mapped[str] = mapped_column(String(10), primary_key=True)
    day_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | approved | posted | skipped
    data_json: Mapped[str] = mapped_column(Text, default="{}")
    post_urn: Mapped[str | None] = mapped_column(String(120), nullable=True)
    posted_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)


METRIC_FIELDS = ("impressions", "reactions", "comments", "reposts", "profile_views", "connections_sent", "connections_accepted", "leads")


class Metric(Base):
    __tablename__ = "metrics"
    date: Mapped[str] = mapped_column(String(10), primary_key=True)
    impressions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reactions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reposts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profile_views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    connections_sent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    connections_accepted: Mapped[int | None] = mapped_column(Integer, nullable=True)
    leads: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)


DEFAULT_SETTINGS = {
    "profile": {
        "name": "", "headline": "", "niche": "", "audience": "", "offer": "", "market": "",
        "proof_points": [], "stories": [], "voice": "Operator, not marketer. Short lines. Specific numbers. Admit what broke. No hype.",
        "banned_phrases": ["I'm excited to announce", "game-changer", "delve", "unlock", "In today's fast-paced world", "Let that sink in"],
        "target_creators": [], "timezone": "UTC",
    },
    "content": {
        "weekly_mix": {"mon": "story", "tue": "carousel", "wed": "contrarian", "thu": "build_in_public", "fri": "debate"},
        "require_approval": True, "auto_apply_mix": False,
    },
    "llm": {"model": "claude-opus-5-5", "effort": "medium", "fallbacks": True},
    "delivery": {"email_to": ""},
    "linkedin": {},
    "brand": {"bg": "#0B1220", "fg": "#FFFFFF", "accent": "#22D3EE", "muted": "#94A3B8", "handle": ""},
}


def deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        out[k] = deep_merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def get_settings(s: Session) -> dict:
    row = s.get(Setting, 1)
    return deep_merge(DEFAULT_SETTINGS, row.data if row else {})


def save_settings(s: Session, data: dict) -> None:
    row = s.get(Setting, 1)
    if not row:
        row = Setting(id=1)
        s.add(row)
    row.data = data
    s.commit()


def init_db() -> None:
    Base.metadata.create_all(engine)
    from . import flows  # noqa: circular at import time only
    with SessionLocal() as s:
        if not s.get(Setting, 1):
            save_settings(s, DEFAULT_SETTINGS)
        existing = {f.slug for f in s.scalars(select(Flow))}
        for slug, flow in flows.DEFAULT_FLOWS.items():
            if slug not in existing:
                f = Flow(slug=slug, name=flow["name"], kind=flow["kind"], enabled=1)
                f.data = flow["data"]
                s.add(f)
        s.commit()
