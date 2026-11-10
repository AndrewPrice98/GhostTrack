"""Normalised ADS-B data model. Units: altitude ft, speed kt, rates ft/min, angles degrees."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Literal

Source = Literal["adsb", "mlat", "tisb", "other"]


@dataclass(frozen=True)
class State:
    icao: str
    callsign: str | None
    lat: float
    lon: float
    alt_baro_ft: float | None
    on_ground: bool
    alt_geom_ft: float | None
    gs_kt: float | None
    track_deg: float | None
    baro_rate_fpm: float | None
    geom_rate_fpm: float | None
    squawk: str | None
    category: str | None
    type_code: str | None
    registration: str | None
    source: Source
    nic: int | None
    nac_p: int | None
    seen_pos_s: float | None
    rssi: float | None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "State":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


@dataclass(frozen=True)
class Snapshot:
    ts: float  # epoch seconds
    aircraft: list[State]

    def to_dict(self) -> dict:
        return {"ts": self.ts, "aircraft": [a.to_dict() for a in self.aircraft]}

    @classmethod
    def from_dict(cls, d: dict) -> "Snapshot":
        return cls(ts=float(d["ts"]), aircraft=[State.from_dict(a) for a in d["aircraft"]])
