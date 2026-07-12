# movie-scraper

A tiny movie release feed. The scraper writes `docs/data/releases.csv` and renders a GitHub Pages-ready static site in `docs/`.

## Run locally

```bash
python movie_scraper.py
```

By default it uses:

- TMDB for movies when `TMDB_API_KEY` is set.
- The Numbers theatrical release schedule without an API key.

To render the site from the current CSV only:

```bash
python movie_scraper.py --site-only
```

## GitHub setup

1. Create a TMDB API key at themoviedb.org.
2. In your GitHub repo, add it as a repository secret named `TMDB_API_KEY`.
3. Enable GitHub Pages with source `Deploy from a branch`, branch `main`, folder `/docs`.
4. Run the workflow in `.github/workflows/update-releases.yml` manually whenever you want to refresh the feed. It commits changed CSV/site output.

## Files

- `movie_scraper.py` - scraper, CSV merger, and static site renderer.
- `sources.json` - source configuration.
- `docs/data/releases.csv` - generated release data.
- `docs/index.html` - generated site.
