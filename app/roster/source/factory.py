"""Pick the roster source from settings."""

from __future__ import annotations

from app.config import Settings
from app.roster.source.base import RosterSource
from app.roster.source.demo import DemoRosterSource
from app.roster.source.real import RealRosterSource


def get_roster_source(settings: Settings) -> RosterSource:
    if settings.data_source == "demo":
        return DemoRosterSource(settings.demo_data_dir)
    return RealRosterSource(settings)
