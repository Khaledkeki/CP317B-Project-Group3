"""Public account details; password hashes never belong in responses."""

from dataclasses import dataclass


@dataclass(frozen=True)
class User:
    id: int
    name: str
    email: str
    username: str
    role: str


@dataclass(frozen=True)
class Vehicle:
    id: int
    fleet_code: str
    name: str
    branch: str
    vehicle_type: str
    category: str
    seats: int
    transmission: str
    fuel_type: str
    daily_rate_cents: int
    status: str
    retired_at: str | None
    version: int

    @property
    def daily_rate(self) -> str:
        return f"{self.daily_rate_cents // 100}.{self.daily_rate_cents % 100:02d}"

    def form_values(self) -> dict:
        from dataclasses import asdict
        return {**asdict(self), "daily_rate": self.daily_rate}
