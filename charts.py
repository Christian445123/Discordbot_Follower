#!/usr/bin/env python3
"""Rendert den 30-Tage-Follower-Verlauf als PNG fuer /statistik social.

matplotlib rendert synchron (CPU-gebunden) - render_follower_chart() muss
daher immer ueber asyncio.to_thread() aufgerufen werden, damit der Bot
waehrenddessen nicht blockiert (siehe bot.py: build_follower_chart_file).
"""
from __future__ import annotations

import io
from datetime import datetime

import matplotlib

matplotlib.use("Agg")  # Kein Display auf dem Server vorhanden/noetig

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

# An Discords Dark-Theme-Embed-Hintergrund angelehnt, damit der Graf nicht als
# heller Block zwischen den dunklen Embed-Feldern haengt.
_BACKGROUND = "#2f3136"
_FOREGROUND = "#dcddde"
_GRID_COLOR = "#4f545c"
_LINE_COLORS = ["#5865F2", "#57F287", "#FEE75C", "#EB459E", "#ED4245"]


def render_follower_chart(series: dict[str, list[tuple[int, int]]]) -> io.BytesIO:
    """series: {"📸 Instagram": [(unix_timestamp, count), ...], ...} - eine
    Linie je Plattform. Gibt einen PNG-Bild-Buffer zurueck (Position 0)."""
    fig, ax = plt.subplots(figsize=(8, 4), dpi=150)
    fig.patch.set_facecolor(_BACKGROUND)
    ax.set_facecolor(_BACKGROUND)

    for i, (label, points) in enumerate(series.items()):
        dates = [datetime.fromtimestamp(ts) for ts, _ in points]
        counts = [count for _, count in points]
        ax.plot(
            dates, counts,
            marker="o", markersize=3, linewidth=1.8,
            label=label, color=_LINE_COLORS[i % len(_LINE_COLORS)],
        )

    ax.set_title("Follower-Verlauf (30 Tage)", color=_FOREGROUND, fontsize=12)
    ax.tick_params(colors=_FOREGROUND, labelsize=8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    ax.grid(True, color=_GRID_COLOR, alpha=0.5, linewidth=0.5)
    for spine in ax.spines.values():
        spine.set_color(_GRID_COLOR)
    ax.legend(facecolor=_BACKGROUND, edgecolor=_GRID_COLOR, labelcolor=_FOREGROUND, fontsize=9)
    fig.autofmt_xdate()
    fig.tight_layout()

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor=_BACKGROUND)
    plt.close(fig)
    buffer.seek(0)
    return buffer
