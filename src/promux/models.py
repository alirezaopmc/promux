from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Any, Dict


class AccountState(str, Enum):
    STANDBY = "standby"
    ACTIVE = "active"
    COOLDOWN = "cooldown"
    DISABLED = "disabled"


@dataclass
class AccountMeta:
    name: str
    enabled: bool = True
    cooldown_until: Optional[datetime] = None
    saved_at: datetime = field(default_factory=datetime.utcnow)
    last_used_at: Optional[datetime] = None
    email: Optional[str] = None
    project_id: Optional[str] = None
    plan_type: str = "STANDARD"

    @property
    def state(self) -> AccountState:
        if not self.enabled:
            return AccountState.DISABLED
        if self.cooldown_until:
            now = datetime.now(self.cooldown_until.tzinfo) if self.cooldown_until.tzinfo else datetime.utcnow()
            if self.cooldown_until > now:
                return AccountState.COOLDOWN
        return AccountState.STANDBY

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "cooldown_until": self.cooldown_until.isoformat() if self.cooldown_until else None,
            "saved_at": self.saved_at.isoformat() if self.saved_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "email": self.email,
            "project_id": self.project_id,
            "plan_type": self.plan_type,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AccountMeta":
        return cls(
            name=data["name"],
            enabled=data.get("enabled", True),
            cooldown_until=datetime.fromisoformat(data["cooldown_until"]) if data.get("cooldown_until") else None,
            saved_at=datetime.fromisoformat(data["saved_at"]) if data.get("saved_at") else datetime.utcnow(),
            last_used_at=datetime.fromisoformat(data["last_used_at"]) if data.get("last_used_at") else None,
            email=data.get("email"),
            project_id=data.get("project_id"),
            plan_type=data.get("plan_type", "STANDARD"),
        )


@dataclass
class QuotaBucket:
    bucket_id: str
    display_name: str
    window: str
    remaining_fraction: float
    reset_time: Optional[str] = None


@dataclass
class QuotaSummary:
    gemini_5h_remaining: float = 1.0
    gemini_weekly_remaining: float = 1.0
    third_party_5h_remaining: float = 1.0
    third_party_weekly_remaining: float = 1.0
    gemini_5h_reset: Optional[str] = None
    gemini_weekly_reset: Optional[str] = None
    third_party_5h_reset: Optional[str] = None
    third_party_weekly_reset: Optional[str] = None

    @property
    def min_short_window(self) -> float:
        return min(self.gemini_5h_remaining, self.third_party_5h_remaining)

    @property
    def min_weekly(self) -> float:
        return min(self.gemini_weekly_remaining, self.third_party_weekly_remaining)


@dataclass
class RotationResult:
    success: bool
    from_account: Optional[str]
    to_account: Optional[str]
    reason: str
    cooldown_until: Optional[datetime] = None
