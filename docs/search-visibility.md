# Search visibility release — 2026-10-05

Canonical site: https://nycopenmicmasterlist.com/

## Baseline

- Finder markup initially contained no mic records; JavaScript fetched `/api/public`.
- robots.txt blocked all `/api/` paths, including the public listings response.
- Sitemap listed only the home and About pages.
- Public-data response inspected on October 5 contained 786 published records across five boroughs. This is a record count, not a claim of 786 distinct active recurring mics.
- A web-search query for the exact domain returned no results in the available search tool. That does not prove Google has not indexed it. Google Search Console was not available, so impressions, Google index coverage and rankings could not be measured.

## Published URL register

- `/`: improved title, description, WebSite markup; finder layout and controls preserved. Separately requested marker changes use individual small pins at supplied coordinates.
- `/about`: links to the crawlable directory and boroughs, named authorship, and factual explanations of host access and confirmation labels; administrator-edited fields remain intact.
- `/nyc-comedy-open-mics`: paginated HTML directory with actual listings.
- `/open-mics/{borough}`: borough-specific listing pages.
- `/mics/{id}`: individual listing detail pages with sources, schedules and update information.
- `/sitemap.xml`: dynamically lists eligible canonical URLs.
- `/robots.txt`: permits `/api/public`, keeps private APIs excluded.

Pages return the same content to search engines and people. No hidden keyword blocks, invented reviews, purchased links or rank guarantees. Structured data describes actual visible page content. Recurring mics with unknown dates do not get invented Event dates.

## Measurement and access still needed

Connect a verified Google Search Console property and submit `https://nycopenmicmasterlist.com/sitemap.xml`. Inspect the home, directory and representative mic URLs; use indexing and performance reports to check real results. No sitemap submission is claimed by this release.

Review search impressions, clicks and indexing for these themes after search engines recrawl: NYC comedy open mics; New York City stand-up open mic; Brooklyn comedy open mics; Manhattan comedy open mics; Queens comedy open mics; open mics in the Bronx; Staten Island comedy open mics. Record dates and compare like-for-like periods. Genuine links from venues and hosts can be earned through useful listings; no messages to other people were sent.

The visible visit counter uses a 1,000 starting offset. Actual tracked visits remain separate and must be used for traffic analysis.

## Primary references

- https://developers.google.com/search/docs/crawling-indexing/javascript/javascript-seo-basics
- https://developers.google.com/search/docs/crawling-indexing/links-crawlable
- https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap
- https://developers.google.com/search/docs/essentials/spam-policies
- https://developers.google.com/search/docs/appearance/structured-data/event

Content-access workflow informed by GEO Content Engineering, based on Eugen Ullrich's OKF knowledge bundle, https://eullrich.com/, CC BY 4.0. Google documentation takes precedence over speculative ranking claims.
