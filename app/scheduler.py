"""In-process scheduler: every 30s, fire any enabled flow whose local time has arrived (10-minute catch-up window)."""
import asyncio
import datetime as dt
import logging
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .db import Flow, SessionLocal, get_settings
from .kits import WEEKDAYS

log = logging.getLogger("studio.scheduler")


def due(now: dt.datetime, flow: Flow) -> str | None:
    """Return the fire key when the flow's schedule is due and not yet fired for it."""
    sch = flow.data.get("schedule") or {}
    if not flow.enabled or not sch.get("time") or WEEKDAYS[now.weekday()] not in (sch.get("days") or []):
        return None
    hh, mm = (int(x) for x in sch["time"].split(":"))
    at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if not (dt.timedelta(0) <= now - at <= dt.timedelta(minutes=10)):
        return None
    key = at.strftime("%Y-%m-%d %H:%M")
    return None if flow.data.get("last_fired") == key else key


def tick(launch) -> list[str]:
    fired = []
    with SessionLocal() as s:
        tz = ZoneInfo(get_settings(s)["profile"].get("timezone") or "UTC")
        now = dt.datetime.now(tz)
        for flow in s.scalars(select(Flow)):
            key = due(now, flow)
            if not key:
                continue
            data = flow.data
            data["last_fired"] = key  # ponytail: single-instance guard; use a DB lock if you ever run two instances
            flow.data = data
            s.commit()
            launch(flow.slug, {"trigger": "schedule", "at": key})
            fired.append(flow.slug)
    return fired


async def loop(launch, interval: int = 30) -> None:
    while True:
        try:
            tick(launch)
        except Exception:  # keep the loop alive whatever happens
            log.exception("scheduler tick failed")
        await asyncio.sleep(interval)
