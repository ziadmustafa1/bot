from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class BinInfo:
    bin: str
    brand: str = "UNKNOWN"
    country: str = "UNKNOWN"
    country_name: str = "UNKNOWN"
    bank: str = "UNKNOWN"
    level: str = "UNKNOWN"
    type: str = "UNKNOWN"
    available: bool = False


async def lookup_bin(base_url: str, bin_value: str, timeout_seconds: float = 5.0) -> BinInfo:
    if not base_url:
        return BinInfo(bin=bin_value)

    url = f"{base_url.rstrip('/')}/{bin_value}"
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()

    return BinInfo(
        bin=str(payload.get("bin") or bin_value),
        brand=str(payload.get("brand") or "UNKNOWN"),
        country=str(payload.get("country") or "UNKNOWN"),
        country_name=str(payload.get("country_name") or "UNKNOWN"),
        bank=str(payload.get("bank") or "UNKNOWN"),
        level=str(payload.get("level") or "UNKNOWN"),
        type=str(payload.get("type") or "UNKNOWN"),
        available=True,
    )


def format_bin_header(info: BinInfo) -> str:
    lines = [
        "BIN INFO",
        f"BIN: {info.bin}",
        f"Brand: {info.brand}",
        f"Country: {info.country_name} ({info.country})",
        f"Bank: {info.bank}",
        f"Level: {info.level}",
        f"Type: {info.type}",
    ]
    if not info.available:
        lines.append("Lookup: unavailable")
    lines.extend(["", "RESULTS"])
    return "\n".join(lines) + "\n"
