"""Temporary contracts. Replace this module's definitions with app.models imports
after checking the team's v2 field names and alert levels.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class AlertLevel(str, Enum):
    STANDARD = "standard"
    IMPORTANT = "important"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Reading:
    ts: datetime
    value: float


@dataclass(frozen=True)
class Forecast:
    rate: float
    value_20: float | None = None
    value_30: float | None = None


@dataclass(frozen=True)
class Alert:
    kind: str
    level: AlertLevel
    value: float
    ts: datetime
