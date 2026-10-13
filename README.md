# SEO Audit

Bulk technical SEO checks for a list of URLs — no browser, no JavaScript, just fast
plain-HTTP fetching with a browser-like User-Agent.

For every URL the actor records:

| Field | What it is |
|---|---|
| `url` | The URL as requested |
| `finalUrl` | URL after following all redirects |
| `statusCode` | Final HTTP status code |
| `redirectCount` / `redirectChain` | How many redirects, and the full chain (`301 http://… -> 200 https://…`) |
| `loadTimeMs` | Time to fetch the page |
| `title` / `titleLength` | Page title and its character count |
| `metaDescription` / `metaDescriptionLength` | Meta description and its length |
| `canonical` | `link rel=canonical` target, if present |
| `robotsMeta` | `meta name=robots` content, if present |
| `h1` / `h1Count` | First H1 text and total H1 count |
| `wordCount` | Words in the visible page text |
| `imagesTotal` / `imagesMissingAlt` | Image count and how many lack alt text |
| `hreflangCount` | Number of `hreflang` alternates |
| `hasViewport` | Whether a viewport meta tag exists |
| `lang` | The `html lang` attribute |
| `ok` / `error` | Success flag and failure reason |

Failed URLs (DNS errors, timeouts, blocked private addresses) are reported as rows
with `ok: false` — one bad URL never stops the batch. Internal/private addresses
are refused by an SSRF guard.

## Input

- **URLs to audit** — one URL per line (up to the max below).
- **Max URLs** — cap on how many URLs are audited (default 50, max 500).

## Pricing

| Event | Price |
|---|---|
| URL audited | **$0.002** per URL successfully audited |

Failed URLs are never charged. You also pay the standard Apify platform usage
costs for the compute used.

## Use cases

- Broken-link and redirect-chain reports across a whole site
- Title / meta-description length audits before a relaunch
- Image alt-text coverage checks
- Hreflang and canonical verification for multilingual sites
- Feeding clean, structured page facts into AI / RAG pipelines
