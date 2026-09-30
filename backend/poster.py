"""Generates an SVG poster from a title's stored hue and metadata."""
from __future__ import annotations

import html


def _wrap(text: str, width: int = 16):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 <= width:
            cur = (cur + " " + w).strip()
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines[:4]


def poster_svg(rec: dict, w: int = 300, h: int = 450) -> str:
    hue = rec.get("poster_hue", 210)
    title = rec.get("title", "Untitled")
    year = rec.get("release_year", "")
    ctype = (rec.get("content_type") or "").upper()
    genres = rec.get("genres", [])
    top_genre = genres[0] if genres else ""

    c1 = f"hsl({hue}, 65%, 28%)"
    c2 = f"hsl({(hue + 40) % 360}, 70%, 12%)"
    accent = f"hsl({hue}, 80%, 62%)"

    lines = _wrap(title, width=14)
    line_h = 34 if len(lines) <= 3 else 28
    start_y = h / 2 - (len(lines) - 1) * line_h / 2
    tspans = "".join(
        f'<text x="{w/2}" y="{start_y + i*line_h}" text-anchor="middle" '
        f'fill="#fff" font-size="{26 if len(lines)<=3 else 22}" font-weight="800" '
        f'font-family="Arial, sans-serif" style="letter-spacing:.5px">'
        f'{html.escape(line)}</text>'
        for i, line in enumerate(lines))

    gid = f"g{hue}"
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">
  <defs>
    <linearGradient id="{gid}" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{c1}"/>
      <stop offset="100%" stop-color="{c2}"/>
    </linearGradient>
    <radialGradient id="vig{hue}" cx="50%" cy="38%" r="75%">
      <stop offset="60%" stop-color="rgba(0,0,0,0)"/>
      <stop offset="100%" stop-color="rgba(0,0,0,0.55)"/>
    </radialGradient>
  </defs>
  <rect width="{w}" height="{h}" fill="url(#{gid})"/>
  <rect width="{w}" height="{h}" fill="url(#vig{hue})"/>
  <circle cx="{w-46}" cy="46" r="3" fill="{accent}" opacity="0.9"/>
  <text x="26" y="50" fill="{accent}" font-size="22" font-weight="900"
        font-family="Arial, sans-serif">N</text>
  <text x="{w-26}" y="50" text-anchor="end" fill="rgba(255,255,255,.85)"
        font-size="12" font-weight="700" font-family="Arial, sans-serif"
        letter-spacing="1">{html.escape(ctype)}</text>
  {tspans}
  <rect x="26" y="{h-70}" width="3" height="40" fill="{accent}"/>
  <text x="40" y="{h-50}" fill="rgba(255,255,255,.9)" font-size="14" font-weight="700"
        font-family="Arial, sans-serif">{html.escape(str(top_genre))}</text>
  <text x="40" y="{h-32}" fill="rgba(255,255,255,.65)" font-size="13"
        font-family="Arial, sans-serif">{html.escape(str(year))}</text>
</svg>'''
