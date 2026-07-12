#!/usr/bin/env python3
"""Fetch recent movies into CSV and render a small static site."""

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


class TableCell:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []

    def text(self) -> str:
        return " ".join(" ".join(self.parts).split())


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[TableCell]] = []
        self.current_row: list[TableCell] | None = None
        self.current_cell: TableCell | None = None
        self.current_href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "tr":
            self.current_row = []
        elif tag in {"td", "th"} and self.current_row is not None:
            self.current_cell = TableCell()
        elif tag == "a" and self.current_cell is not None:
            self.current_href = attrs_dict.get("href")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self.current_href = None
        elif tag in {"td", "th"} and self.current_row is not None and self.current_cell is not None:
            self.current_row.append(self.current_cell)
            self.current_cell = None
        elif tag == "tr" and self.current_row is not None:
            if self.current_row:
                self.rows.append(self.current_row)
            self.current_row = None

    def handle_data(self, data: str) -> None:
        if self.current_cell is None:
            return
        self.current_cell.parts.append(data)
        text = clean_text(data)
        if self.current_href and text:
            self.current_cell.links.append((text, self.current_href))


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


def fetch_text(url: str, headers: dict[str, str] | None = None) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "movie-scraper/1.0 (+https://github.com/)",
            **(headers or {}),
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\r", " ").replace("\n", " ").strip()
    return " ".join(text.split())


def parse_date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return None


def parse_numbers_date(value: str, fallback_year: int) -> dt.date | None:
    value = clean_text(value).replace("\xa0", " ")
    for fmt in ("%B %d %Y", "%b %d %Y"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    for fmt in ("%B %d %Y", "%b %d %Y"):
        try:
            return dt.datetime.strptime(f"{value} {fallback_year}", fmt).date()
        except ValueError:
            pass
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
        print("Skipping TMDB: set TMDB_API_KEY to enable movie discovery.", file=sys.stderr)
        return []

    base_url = config.get("base_url", "https://api.themoviedb.org/3")
    image_base_url = config.get("image_base_url", "https://image.tmdb.org/t/p/w342")
    start_date = today_utc() - dt.timedelta(days=days_back)
    end_date = today_utc()

    rows: list[dict[str, str]] = []
    spec = {
        "path": "/discover/movie",
        "date_field": "primary_release_date",
        "title_field": "title",
        "params": {
            "primary_release_date.gte": start_date.isoformat(),
            "primary_release_date.lte": end_date.isoformat(),
            "sort_by": "primary_release_date.desc",
        },
    }
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
            kind="movie",
            release_date=item.get(spec["date_field"], ""),
            source="TMDB",
            source_url=f"https://www.themoviedb.org/movie/{item.get('id')}",
            summary=item.get("overview", ""),
            poster_url=f"{image_base_url}{poster_path}" if poster_path else "",
        )
        if row:
            rows.append(row)
    return rows


def fetch_the_numbers(config: dict[str, Any], days_back: int) -> list[dict[str, str]]:
    url = config.get("url", "https://www.the-numbers.com/movies/release-schedule")
    start_date = today_utc() - dt.timedelta(days=days_back)
    end_date = today_utc()
    fallback_year = int(config.get("year", today_utc().year))
    html_doc = fetch_text(url)

    parser = TableParser()
    parser.feed(html_doc)
    parser.close()

    rows: list[dict[str, str]] = []
    current_date: dt.date | None = None
    for table_row in parser.rows:
        cells = [cell for cell in table_row if cell.text()]
        if len(cells) < 2:
            continue

        maybe_date = parse_numbers_date(cells[0].text(), fallback_year)
        movie_cell = cells[1]
        if maybe_date is not None:
            current_date = maybe_date
        elif current_date is None:
            continue
        else:
            movie_cell = cells[0]

        if current_date < start_date or current_date > end_date:
            continue

        for title, href in movie_cell.links:
            title = title.removesuffix("(IMAX)").strip()
            if not title or title.lower() in {"movie", "release date"}:
                continue
            row = normalize_row(
                title=title,
                kind="movie",
                release_date=current_date.isoformat(),
                source="The Numbers",
                source_url=urllib.parse.urljoin(url, href),
                summary="",
                poster_url="",
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
        if clean_text(row.get("kind", "")).casefold() != "movie":
            continue
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
      <p class="eyebrow">Movie release feed</p>
      <h1>Fresh Screen Releases</h1>
    </div>
    <a class="csv-link" href="{csv_href}">CSV</a>
  </header>
  <main>
    <section class="stats" aria-label="Feed stats">
      <div><strong>{len(rows)}</strong><span>tracked titles</span></div>
      <div><strong>{generated_at[:10]}</strong><span>last build</span></div>
    </section>
    <section class="grid" aria-label="Movies">
      {cards or '<p class="empty">No releases yet. Run the scraper to refresh movie sources.</p>'}
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
  background: #0f0f0f;
  color: #f1f1f1;
}

a {
  color: inherit;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  position: sticky;
  top: 0;
  z-index: 2;
  padding: 18px clamp(18px, 5vw, 64px);
  background: rgba(15, 15, 15, 0.94);
  border-bottom: 1px solid #272727;
  backdrop-filter: blur(14px);
}

.eyebrow {
  margin: 0 0 6px;
  color: #ff4747;
  font-size: 0.78rem;
  font-weight: 700;
  letter-spacing: 0;
  text-transform: uppercase;
}

h1 {
  margin: 0;
  font-size: clamp(1.65rem, 3vw, 3.2rem);
  line-height: 1;
  color: #ffffff;
}

.csv-link {
  min-height: 40px;
  padding: 10px 16px;
  border: 1px solid #3a3a3a;
  border-radius: 8px;
  text-decoration: none;
  font-weight: 700;
  background: #ff0000;
  color: #ffffff;
  box-shadow: 0 10px 30px rgba(255, 0, 0, 0.2);
  transition: background 0.18s ease, transform 0.18s ease;
}

.csv-link:hover {
  background: #cc0000;
  transform: translateY(-1px);
}

main {
  width: min(1320px, calc(100% - 32px));
  margin: 0 auto;
  padding: 22px 0 48px;
}

.stats {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  margin-bottom: 22px;
}

.stats div {
  padding: 16px 18px;
  background: #181818;
  border: 1px solid #2b2b2b;
  border-radius: 8px;
}

.stats strong,
.stats span {
  display: block;
}

.stats strong {
  font-size: 1.35rem;
  color: #ffffff;
}

.stats span {
  margin-top: 4px;
  color: #aaaaaa;
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 22px 16px;
}

.release-card {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  overflow: hidden;
  background: #181818;
  border: 1px solid transparent;
  border-radius: 8px;
  transition: transform 0.18s ease, border-color 0.18s ease, background 0.18s ease;
}

.release-card:hover {
  transform: translateY(-3px);
  background: #202020;
  border-color: #3a3a3a;
}

.poster {
  aspect-ratio: 16 / 9;
  background: #242424;
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
    radial-gradient(circle at 24% 24%, rgba(255, 255, 255, 0.12), transparent 30%),
    linear-gradient(135deg, #262626 0%, #151515 46%, #8f0000 47%, #ff0000 56%, #2a2a2a 57%, #171717 100%);
}

.release-copy {
  display: flex;
  flex-direction: column;
  min-width: 0;
  padding: 12px 4px 2px;
}

.meta {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  color: #aaaaaa;
  font-size: 0.76rem;
  font-weight: 700;
  text-transform: uppercase;
}

h2 {
  margin: 8px 0 8px;
  font-size: 1.02rem;
  line-height: 1.25;
  color: #ffffff;
}

.release-copy p {
  display: -webkit-box;
  flex: 1;
  margin: 0;
  overflow: hidden;
  color: #b9b9b9;
  font-size: 0.9rem;
  line-height: 1.45;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
}

footer {
  margin-top: 12px;
  color: #ff4747;
  font-size: 0.84rem;
  font-weight: 700;
}

.empty {
  margin: 0;
  padding: 24px;
  background: #181818;
  border: 1px solid #2b2b2b;
  border-radius: 8px;
  color: #b9b9b9;
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

  .grid {
    grid-template-columns: 1fr;
  }
}
"""


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"sources": [{"type": "the_numbers"}]}
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_sources(config: dict[str, Any], days_back: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for source in config.get("sources", []):
        source_type = source.get("type")
        try:
            if source_type == "tmdb":
                rows.extend(fetch_tmdb(source, days_back))
            elif source_type == "the_numbers":
                rows.extend(fetch_the_numbers(source, days_back))
            else:
                print(f"Skipping unknown source type: {source_type}", file=sys.stderr)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            print(f"Source failed ({source_type}): {error}", file=sys.stderr)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Update movie CSV and static site.")
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
