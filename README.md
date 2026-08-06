# movie-scraper

A simple movie release feed that updates a static GitHub Pages site.

## Update The Feed

```bash
python movie_scraper.py
```

The GitHub Actions workflow also refreshes the feed automatically every 6 hours.

To rebuild the site from the current CSV without scraping:

```bash
python movie_scraper.py --site-only
```

## Website

The site is generated in `docs/` for GitHub Pages.

## Files

- `movie_scraper.py` - scraper and site generator.
- `docs/data/releases.csv` - movie data.
- `docs/index.html` - website page.
