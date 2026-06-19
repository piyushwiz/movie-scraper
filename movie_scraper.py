#!/usr/bin/env python3
"""Fetch recent movies/series into CSV and render a small static site."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
from html.parser import HTMLParser
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


CSV_FIELDS = [
    "title",
    "kind",
    "release_date",
    "source",
    "source_url",
    "summary",
    "poster_url",
    "updated_at",
]


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def get_text(self) -> str:
        return " ".join(" ".join(self.parts).split())


def today_utc() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def fetch_json(url: str, headers: dict[str, str] | None = None) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "movie-scraper/1.0 (+https://github.com/)",
            **(headers or {}),
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\r", " ").replace("\n", " ").strip()
    return " ".join(text.split())


def strip_html(value: Any) -> str:
    parser = TextExtractor()
    parser.feed(clean_text(value))
    parser.close()
    return parser.get_text()


def parse_date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return None


def row_key(row: dict[str, str]) -> tuple[str, str, str]:
    return (
        row.get("title", "").casefold(),
        row.get("kind", "").casefold(),
        row.get("release_date", ""),
    )


def normalize_row(
    *,
    title: str,
    kind: str,
    release_date: str,
    source: str,
    source_url: str,
    summary: str = "",
    poster_url: str = "",
) -> dict[str, str] | None:
    title = clean_text(title)
    release = parse_date(release_date)
    if not title or release is None:
        return None

    return {
        "title": title,
        "kind": clean_text(kind) or "unknown",
        "release_date": release.isoformat(),
        "source": clean_text(source),
        "source_url": clean_text(source_url),
        "summary": clean_text(summary),
        "poster_url": clean_text(poster_url),
        "updated_at": iso_now(),
    }


def fetch_tmdb(config: dict[str, Any], days_back: int) -> list[dict[str, str]]:
    api_key = os.environ.get("TMDB_API_KEY", "").strip()
    if not api_key:
        print("Skipping TMDB: set TMDB_API_KEY to enable movie/TV discovery.", file=sys.stderr)
        return []

    base_url = config.get("base_url", "https://api.themoviedb.org/3")
    image_base_url = config.get("image_base_url", "https://image.tmdb.org/t/p/w342")
    start_date = today_utc() - dt.timedelta(days=days_back)
    end_date = today_utc()

    rows: list[dict[str, str]] = []
    specs = [
        {
            "path": "/discover/movie",
            "kind": "movie",
            "date_field": "primary_release_date",
            "title_field": "title",
            "params": {
                "primary_release_date.gte": start_date.isoformat(),
                "primary_release_date.lte": end_date.isoformat(),
                "sort_by": "primary_release_date.desc",
            },
        },
        {
            "path": "/discover/tv",
            "kind": "series",
            "tmdb_path": "tv",
            "date_field": "first_air_date",
            "title_field": "name",
            "params": {
                "first_air_date.gte": start_date.isoformat(),
                "first_air_date.lte": end_date.isoformat(),
                "sort_by": "first_air_date.desc",
            },
        },
    ]

    for spec in specs:
        params = {
            "api_key": api_key,
            "language": config.get("language", "en-US"),
            "page": "1",
            **spec["params"],
        }
        url = f"{base_url}{spec['path']}?{urllib.parse.urlencode(params)}"
        data = fetch_json(url)
        for item in data.get("results", []):
            poster_path = item.get("poster_path") or ""
            row = normalize_row(
                title=item.get(spec["title_field"], ""),
                kind=spec["kind"],
                release_date=item.get(spec["date_field"], ""),
                source="TMDB",
                source_url=f"https://www.themoviedb.org/{spec.get('tmdb_path', spec['kind'])}/{item.get('id')}",
                summary=item.get("overview", ""),
                poster_url=f"{image_base_url}{poster_path}" if poster_path else "",
            )
            if row:
                rows.append(row)
    return rows


def fetch_tvmaze(config: dict[str, Any], days_back: int) -> list[dict[str, str]]:
    base_url = config.get("base_url", "https://api.tvmaze.com")
    countries = config.get("countries", ["US"])
    start_date = today_utc() - dt.timedelta(days=days_back)
    rows: list[dict[str, str]] = []

    for day_offset in range(days_back + 1):
        date = start_date + dt.timedelta(days=day_offset)
        for country in countries:
            params = urllib.parse.urlencode({"country": country, "date": date.isoformat()})
            url = f"{base_url}/schedule?{params}"
            data = fetch_json(url)
            for episode in data:
                show = episode.get("show") or {}
                image = show.get("image") or {}
                row = normalize_row(
                    title=show.get("name", ""),
                    kind="series",
                    release_date=episode.get("airdate", ""),
                    source=f"TVMaze {country}",
                    source_url=show.get("url", ""),
                    summary=strip_html(show.get("summary", "")),
                    poster_url=image.get("medium") or image.get("original") or "",
                )
                if row:
                    rows.append(row)
    return rows


def load_existing_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [{field: row.get(field, "") for field in CSV_FIELDS} for row in csv.DictReader(handle)]


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda row: row.get("release_date", ""), reverse=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def merge_rows(existing: list[dict[str, str]], incoming: list[dict[str, str]], keep_days: int) -> list[dict[str, str]]:
    cutoff = today_utc() - dt.timedelta(days=keep_days)
    merged: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in [*existing, *incoming]:
        release = parse_date(row.get("release_date", ""))
        if release is None or release < cutoff:
            continue
        merged[row_key(row)] = {field: clean_text(row.get(field, "")) for field in CSV_FIELDS}
    return list(merged.values())


def render_site(csv_path: Path, output_path: Path, rows: list[dict[str, str]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cards = "\n".join(render_card(row) for row in rows[:120])
    generated_at = html.escape(iso_now())
    csv_href = html.escape(os.path.relpath(csv_path, output_path.parent))
    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Fresh Screen Releases</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header class="topbar">
    <div>
      <p class="eyebrow">Daily release feed</p>
      <h1>Fresh Screen Releases</h1>
    </div>
    <a class="csv-link" href="{csv_href}">CSV</a>
  </header>
  <main>
    <section class="stats" aria-label="Feed stats">
      <div><strong>{len(rows)}</strong><span>tracked titles</span></div>
      <div><strong>{generated_at[:10]}</strong><span>last build</span></div>
    </section>
    <section class="grid" aria-label="Movies and series">
      {cards or '<p class="empty">No releases yet. Run the scraper after adding a source key.</p>'}
    </section>
  </main>
</body>
</html>
"""
    output_path.write_text(html_doc, encoding="utf-8")
    css_path = output_path.with_name("style.css")
    if not css_path.exists():
        css_path.write_text(DEFAULT_CSS, encoding="utf-8")


def render_card(row: dict[str, str]) -> str:
    title = html.escape(row.get("title", "Untitled"))
    kind = html.escape(row.get("kind", "unknown").title())
    release_date = html.escape(row.get("release_date", ""))
    source = html.escape(row.get("source", ""))
    source_url = html.escape(row.get("source_url", ""))
    summary = html.escape(row.get("summary", ""))
    poster_url = html.escape(row.get("poster_url", ""))
    poster = f'<img src="{poster_url}" alt="" loading="lazy">' if poster_url else '<div class="poster-fallback"></div>'
    source_link = f'<a href="{source_url}" target="_blank" rel="noopener">{source}</a>' if source_url else source
    return f"""<article class="release-card">
  <div class="poster">{poster}</div>
  <div class="release-copy">
    <div class="meta"><span>{kind}</span><time datetime="{release_date}">{release_date}</time></div>
    <h2>{title}</h2>
    <p>{summary}</p>
    <footer>{source_link}</footer>
  </div>
</article>"""


DEFAULT_CSS = """* {
  box-sizing: border-box;
}

body {
  margin: 0;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: #f5f7f8;
  color: #182126;
}

a {
  color: inherit;
}

.topbar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  padding: 32px clamp(18px, 5vw, 64px) 24px;
  background: #ffffff;
  border-bottom: 1px solid #dbe2e6;
}

.eyebrow {
  margin: 0 0 8px;
  color: #55707d;
  font-size: 0.78rem;
  font-weight: 700;
  letter-spacing: 0;
  text-transform: uppercase;
}

h1 {
  margin: 0;
  font-size: clamp(2rem, 4vw, 4.5rem);
  line-height: 0.95;
}

.csv-link {
  min-height: 40px;
  padding: 10px 14px;
  border: 1px solid #a9b8bf;
  border-radius: 8px;
  text-decoration: none;
  font-weight: 700;
  background: #eff4f6;
}

main {
  width: min(1180px, calc(100% - 32px));
  margin: 0 auto;
  padding: 24px 0 48px;
}

.stats {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 20px;
}

.stats div {
  padding: 16px;
  background: #ffffff;
  border: 1px solid #dbe2e6;
  border-radius: 8px;
}

.stats strong,
.stats span {
  display: block;
}

.stats strong {
  font-size: 1.35rem;
}

.stats span {
  margin-top: 4px;
  color: #55707d;
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 14px;
}

.release-card {
  display: grid;
  grid-template-columns: 96px 1fr;
  min-height: 170px;
  overflow: hidden;
  background: #ffffff;
  border: 1px solid #dbe2e6;
  border-radius: 8px;
}

.poster {
  background: #dce6ea;
}

.poster img,
.poster-fallback {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.poster-fallback {
  background:
    linear-gradient(135deg, #31444f 0%, #31444f 48%, #d94f45 49%, #d94f45 54%, #f2c14e 55%, #f2c14e 100%);
}

.release-copy {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 14px;
}

.meta {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  color: #55707d;
  font-size: 0.78rem;
  font-weight: 700;
  text-transform: uppercase;
}

h2 {
  margin: 8px 0 8px;
  font-size: 1.02rem;
  line-height: 1.2;
}

.release-copy p {
  display: -webkit-box;
  flex: 1;
  margin: 0;
  overflow: hidden;
  color: #44555d;
  font-size: 0.9rem;
  line-height: 1.45;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
}

footer {
  margin-top: 12px;
  color: #2b6777;
  font-size: 0.84rem;
  font-weight: 700;
}

.empty {
  margin: 0;
  padding: 24px;
  background: #ffffff;
  border: 1px solid #dbe2e6;
  border-radius: 8px;
}

@media (max-width: 640px) {
  .topbar {
    display: block;
  }

  .csv-link {
    display: inline-flex;
    align-items: center;
    margin-top: 18px;
  }

  .stats {
    grid-template-columns: 1fr;
  }
}
"""


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"sources": [{"type": "tvmaze", "countries": ["US"]}]}
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_sources(config: dict[str, Any], days_back: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for source in config.get("sources", []):
        source_type = source.get("type")
        try:
            if source_type == "tmdb":
                rows.extend(fetch_tmdb(source, days_back))
            elif source_type == "tvmaze":
                rows.extend(fetch_tvmaze(source, days_back))
            else:
                print(f"Skipping unknown source type: {source_type}", file=sys.stderr)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            print(f"Source failed ({source_type}): {error}", file=sys.stderr)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Update movie/series CSV and static site.")
    parser.add_argument("--config", default="sources.json", help="Source configuration JSON.")
    parser.add_argument("--csv", default="docs/data/releases.csv", help="CSV output path.")
    parser.add_argument("--site", default="docs/index.html", help="Static site HTML output path.")
    parser.add_argument("--days-back", type=int, default=7, help="Fetch releases from the last N days.")
    parser.add_argument("--keep-days", type=int, default=45, help="Keep releases from the last N days.")
    parser.add_argument("--site-only", action="store_true", help="Render the site from the existing CSV.")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    existing = load_existing_csv(csv_path)
    incoming: list[dict[str, str]] = []
    if not args.site_only:
        incoming = fetch_sources(load_config(Path(args.config)), args.days_back)

    rows = merge_rows(existing, incoming, args.keep_days)
    write_csv(csv_path, rows)
    render_site(csv_path, Path(args.site), rows)
    print(f"Saved {len(rows)} releases to {csv_path} and rendered {args.site}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
