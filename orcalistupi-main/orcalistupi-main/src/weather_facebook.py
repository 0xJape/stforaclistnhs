from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import re
import uuid
from base64 import b64decode
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold().removeprefix("city of "))


def _streak(rows: list[dict[str, Any]]) -> int:
    run = maximum = 0
    for row in rows:
        wet = float(row.get("PRECIPITATION_MM") or 0) >= 5 or float(row.get("RAIN_PROBABILITY_PCT") or 0) >= 70
        run = run + 1 if wet else 0
        maximum = max(maximum, run)
    return maximum


def _photo(municipality: str, rows: list[dict[str, Any]], streak: int, map_image: bytes) -> bytes:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = [datetime.fromisoformat(str(row["DATE"])).strftime("%b %d") for row in rows]
    rain = [float(row.get("PRECIPITATION_MM") or 0) for row in rows]
    probability = [float(row.get("RAIN_PROBABILITY_PCT") or 0) for row in rows]
    wet = [value >= 5 or chance >= 70 for value, chance in zip(rain, probability)]
    fig = plt.figure(figsize=(12, 12), facecolor="#071014")
    layout = fig.add_gridspec(2, 1, height_ratios=(1.1, .9), hspace=.15)
    map_axis = fig.add_subplot(layout[0])
    map_axis.imshow(plt.imread(io.BytesIO(map_image), format="jpeg"))
    map_axis.set_axis_off()
    map_axis.set_title("SOUTH COTABATO RISK MAP", loc="left", color="white", fontsize=15, fontweight="bold", pad=12)
    axis = fig.add_subplot(layout[1])
    axis.set_facecolor("#0b1820")
    bars = axis.bar(labels, rain, color=["#42c8f5" if value else "#274653" for value in wet], width=.68, zorder=3)
    for bar, value in zip(bars, rain):
        if value >= 1:
            axis.text(bar.get_x() + bar.get_width() / 2, value + .35, f"{value:.0f}", ha="center", color="#cdeef8", fontsize=8)
    second = axis.twinx()
    second.plot(labels, probability, color="#ffca5c", marker="o", markersize=4.5, linewidth=2.5, zorder=4)
    second.fill_between(labels, probability, color="#ffca5c", alpha=.07)
    axis.set_title(municipality.upper(), loc="left", color="white", fontsize=22, fontweight="bold", pad=23)
    axis.text(0, 1.015, "16-DAY RAINFALL OUTLOOK  •  ORACLIS WEATHER INTELLIGENCE", transform=axis.transAxes, color="#62d6ff", fontsize=10, fontweight="bold")
    axis.text(1, 1.015, f"{streak}-DAY WET STREAK", transform=axis.transAxes, ha="right", color="#ffca5c", fontsize=10, fontweight="bold")
    axis.set_ylabel("RAINFALL  /  MM", color="#8ca6b2", fontsize=9, labelpad=12)
    second.set_ylabel("RAIN PROBABILITY  /  %", color="#c8a34e", fontsize=9, labelpad=12)
    second.set_ylim(0, 100)
    axis.tick_params(colors="#8ca6b2", axis="x", rotation=45, labelsize=8)
    axis.tick_params(colors="#8ca6b2", axis="y", labelsize=8)
    second.tick_params(colors="#c8a34e", labelsize=8)
    axis.grid(axis="y", color="white", alpha=.08, linewidth=.8, zorder=0)
    for edge in (*axis.spines.values(), *second.spines.values()):
        edge.set_visible(False)
    fig.text(.065, .025, "ORACLIS  •  MONITOR LGU AND PAGASA ADVISORIES", color="#77909b", fontsize=8, fontweight="bold")
    fig.tight_layout(rect=(.025, .045, .975, .98))
    output = io.BytesIO()
    fig.savefig(output, format="png", dpi=100, facecolor=fig.get_facecolor())
    plt.close(fig)
    return output.getvalue()


def _multipart(metadata: dict[str, Any], photo: bytes) -> tuple[bytes, str]:
    boundary = "----ORACLIS" + uuid.uuid4().hex
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"metadata\"\r\nContent-Type: application/json\r\n\r\n".encode() + json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode() + b"\r\n",
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"oraclis-weather-warning.png\"\r\nContent-Type: image/png\r\n\r\n".encode() + photo + b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    return b"".join(parts), boundary


def publish_weather_warning(municipality: str, weather_loader: Callable[[int], list[dict[str, Any]]], map_image: str = "", reported_cases: int = 0) -> dict[str, Any]:
    if os.getenv("MAKE_ALERTS_ENABLED", "false").casefold() != "true":
        raise ValueError("Facebook publishing is disabled. Set MAKE_ALERTS_ENABLED=true only after approval.")
    webhook = os.getenv("MAKE_WEBHOOK_URL", "").strip()
    secret = os.getenv("MAKE_WEBHOOK_SECRET", "").strip()
    if not webhook.startswith("https://") or not secret:
        raise ValueError("Secure Make.com webhook configuration is incomplete.")
    rows = [row for row in weather_loader(16) if _key(str(row.get("MUNICIPALITY", ""))) == _key(municipality)]
    if len(rows) != 16:
        raise ValueError("Complete 16-day municipality forecast is unavailable.")
    streak = _streak(rows)
    if streak < 3:
        raise ValueError("Warning no longer meets three-day continuous-rain threshold.")
    start, end = rows[0]["DATE"], rows[-1]["DATE"]
    event_id = f"WEATHER_RAIN:{_key(municipality)}:{start}:{end}"
    warning = "Possible dengue outbreak risk: increased vigilance is advised." if reported_cases else "Possible dengue outbreak risk: monitor conditions and report suspected cases promptly."
    caption_fil = f"DENGUE AWARENESS AT BABALA SA PANGANIB — {municipality}\n\nMay naiulat na {reported_cases} aggregate dengue cases sa {municipality}. May forecast na {streak} magkakasunod na araw ng pag-ulan mula {start} hanggang {end}. Posibleng tumaas ang panganib ng dengue; mag-ingat at agad mag-ulat ng hinihinalang kaso.\n\nMaaaring mag-iwan ang ulan ng naipong tubig na pamugaran ng lamok na nagdadala ng dengue. Alisin o takpan ang mga lalagyan at lugar na maaaring maimbakan ng tubig, gumamit ng proteksiyon laban sa lamok lalo na sa araw, at sundin ang mga abiso ng LGU at PAGASA. Bantayan ang mataas na lagnat, matinding sakit ng ulo, pananakit sa likod ng mata, kalamnan o kasukasuan, pantal, o hindi pangkaraniwang pagdurugo; agad kumonsulta kung may sintomas.\n\nAng awtomatikong dengue risk notice na ito ay hindi opisyal na emergency advisory, diagnosis, o kumpirmadong deklarasyon ng outbreak."
    metadata = {"event_id": event_id, "event_type": "dengue_weather_risk", "generated_at": datetime.now(timezone.utc).isoformat(), "municipality": municipality, "forecast_start": start, "forecast_end": end, "wet_days": streak, "reported_cases": reported_cases, "possible_outbreak_warning": warning, "facebook_message": caption_fil}
    if not map_image.startswith("data:image/jpeg;base64,"):
        raise ValueError("A current map capture is required.")
    try:
        map_bytes = b64decode(map_image.partition(",")[2], validate=True)
    except ValueError as exc:
        raise ValueError("Map capture is invalid.") from exc
    if not map_bytes or len(map_bytes) > 3_000_000:
        raise ValueError("Map capture size is invalid.")
    photo = _photo(municipality, rows, streak, map_bytes)
    body, boundary = _multipart(metadata, photo)
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    request = Request(webhook, data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "X-ORACLIS-Signature": "sha256=" + signature, "X-ORACLIS-Event-ID": event_id, "User-Agent": "ORACLIS/1.0"}, method="POST")
    try:
        with urlopen(request, timeout=30) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"Make.com returned HTTP {response.status}.")
    except HTTPError as exc:
        raise RuntimeError(f"Make.com returned HTTP {exc.code}.") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError("Make.com webhook is unavailable.") from exc
    return {"status": "sent", "event_id": event_id, "photo_bytes": len(photo)}
