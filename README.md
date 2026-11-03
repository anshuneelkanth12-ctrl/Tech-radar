# Tech Radar

A website that keeps itself up to date with new gadgets and accessories (with prices), the tech stories several outlets are covering, and what the tech crowd is discussing. It runs free on GitHub, with no server and no manual input after setup.

## What happens every 4 hours

1. GitHub re-tests the extraction rules. If a test fails, the update stops and the site keeps its previous data.
2. Each of the 16 feeds in `scraper/config.yaml` is read once: Indian and global tech outlets, a deals feed and Hacker News.
3. Every story gets a category (Phones, Audio, Wearables, Accessories, AI & Software and others).
4. Launches, sales, reviews, upcoming devices and rumours become one entry per product.
5. A price is recorded only when an article states it for that product. The sentence and link are kept as proof.
6. Stories covered by two or more different outlets become **Big stories**.
7. Every feed is health-checked. Broken or quiet feeds are flagged on the Sources page instead of silently disappearing.
8. The data is saved to this repository and the site is republished.

## How accuracy is protected

- A price needs a sentence that names the product and states its price ("is priced at", "starts at", "drops to"). Thresholds ("under Rs 20,000"), bundled extras ("worth Rs 799"), currency conversions ("around Rs 19,000") and other products' prices are ignored.
- Rumoured and upcoming products never get a price.
- Each price shows who reported it and when. Other outlets reporting the same price are listed as confirmations.
- Shop links open a search on Amazon.in, Flipkart and Google Shopping. Nothing is scraped from stores, and store prices change, so check before buying or quoting.

## One-time setup (about 15 minutes, no coding)

1. **Create a GitHub account** at github.com (free) and sign in.
2. **Create a repository.** Click **+** (top right), then **New repository**. Name it `tech-radar`, select **Public**, and click **Create repository**. GitHub Pages is free for public repositories.
3. **Turn on the website.** In the repository, open **Settings**, then **Pages**. Under **Build and deployment**, set **Source** to **GitHub Actions**.
4. **Upload the files.** Open the **Code** tab and click **uploading an existing file**. Drag in everything inside the unzipped `tech-radar` folder, including the `.github` folder, then click **Commit changes**. On a Mac, Finder hides folders whose names start with a dot: press **Cmd + Shift + .** in Finder to show `.github` before you drag.
5. **Watch the first update.** Open the **Actions** tab. **Update Tech Radar** starts by itself after the upload. If GitHub asks you to enable workflows, click the button, then open **Update Tech Radar** and click **Run workflow**. A green tick after 2 to 3 minutes means it worked.
6. **Open your site** at `https://YOUR-USERNAME.github.io/tech-radar/`. The exact link is in **Settings**, then **Pages**.

If the Actions tab doesn't list **Update Tech Radar**, the `.github` folder didn't upload: see [Workflow file missing](#workflow-file-missing).

## Using the site

- **Today**: gadgets and accessories launched in the last 4 days with their price tags, big stories, Hacker News, and devices that are coming soon.
- **Launches**: every tracked product, filterable by category and status, with the proof sentence, articles and shop links.
- **Prices**: a sortable price table. **Download CSV** or **Copy CSV** for spreadsheets.
- **News**: every story from the last 21 days, filterable by category and outlet.
- **Saved**: everything you starred with ☆. **Copy as research notes** gives you titles and links to paste into a script. Saved items stay in that browser.
- **Sources**: the health of every feed.

Press `/` to jump to the search box.

## Changing settings

Open `scraper/config.yaml` on GitHub, click the pencil icon, edit, and click **Commit changes**. The site rebuilds within a few minutes.

- **Add a source:** copy a line under `sources:` and change the name and address. Any RSS or Atom feed works, including a YouTube channel (example at the bottom of the file).
- **Remove a source:** delete its line.
- **US or UK stores:** change `region: IN` to `US` or `UK`.
- **Keep stories longer:** change `news_days`.
- **Update right now:** **Actions**, then **Update Tech Radar**, then **Run workflow**.
- **Change the schedule:** edit the `cron` line in `.github/workflows/update.yml`. Times are UTC: `17 */4 * * *` means every 4 hours at 17 minutes past.

## When something goes wrong

- **A feed shows a problem on the Sources page.** *Stale* means it answers but hasn't published anything new for 5 days. *Failed last run* is retried automatically. *Failing* means 3 failed runs in a row, so the address has probably changed: replace or remove it in `config.yaml`.
- **An update fails.** GitHub emails you, and the site keeps the previous data. The site also shows a warning when its data is more than 10 hours old.
- **The schedule stopped.** GitHub pauses scheduled workflows in repositories with no activity for 60 days and emails you. Click **Enable workflow** in the Actions tab.
- **The "Publish the site" step fails.** Check that **Settings**, **Pages**, **Source** is set to **GitHub Actions**.

### Workflow file missing

In the repository, click **Add file**, then **Create new file**. Name it `.github/workflows/update.yml`, paste the text below, and click **Commit changes**.

```yaml
name: Update Tech Radar

# Runs every 4 hours, when you press "Run workflow", and when you change the code or settings.
on:
  schedule:
    - cron: "17 */4 * * *"   # every 4 hours at 17 minutes past (UTC)
  workflow_dispatch:
  push:
    branches: [main]
    paths:
      - "scraper/**"
      - "tests/**"
      - "docs/index.html"
      - "requirements.txt"
      - ".github/workflows/update.yml"

permissions:
  contents: write    # save the updated data file
  pages: write       # publish the site
  id-token: write    # required by GitHub Pages

concurrency:
  group: tech-radar-update
  cancel-in-progress: false

jobs:
  update:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    environment:
      name: github-pages
      url: ${{ steps.deploy.outputs.page_url }}
    steps:
      - name: Get the code
        uses: actions/checkout@v7.0.1
        with:
          persist-credentials: true

      - name: Set up Python
        uses: actions/setup-python@v7.0.0
        with:
          python-version: "3.12"
          cache: pip

      - name: Install
        run: pip install -r requirements.txt

      - name: Self-test (the update stops here if the extraction rules are broken)
        run: python -m unittest discover -s tests -v

      - name: Read the sources and rebuild the data
        run: python -m scraper.run

      - name: Save the data to the repository
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add docs/data/feed.json
          if git diff --cached --quiet; then
            echo "No data changes."
          else
            git commit -m "Data update $(date -u +'%Y-%m-%d %H:%M UTC')"
            git pull --rebase --autostash
            git push
          fi

      - name: Prepare GitHub Pages
        uses: actions/configure-pages@v6.0.0

      - name: Package the site
        uses: actions/upload-pages-artifact@v5.0.0
        with:
          path: docs

      - name: Publish the site
        id: deploy
        uses: actions/deploy-pages@v5.0.1
```

## Upgrades that need your approval

- **Live store prices.** Amazon's Creators API requires an Amazon Associates account with at least 10 qualifying sales in the last 30 days. Flipkart's Affiliate API requires an approved Flipkart affiliate account.
- **AI summaries and video angles.** Needs an Anthropic API key and is billed per use.

## For developers

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v   # self-tests, including a full run on saved feeds
python -m scraper.run                      # live update into docs/data/feed.json
python -m http.server -d docs 8000         # then open http://localhost:8000
```

| Path | What it is |
|---|---|
| `scraper/config.yaml` | Sources, region, stores, retention |
| `scraper/sources.py` | Fetches and parses feeds |
| `scraper/extract.py` | Categories, product names, status and prices |
| `scraper/trending.py` | Groups cross-outlet coverage into big stories |
| `scraper/run.py` | The update run and source health checks |
| `docs/index.html` | The website (one file) |
| `docs/data/feed.json` | The data, rewritten by every update |
| `tests/` | Self-tests and saved feeds from 2 November 2026 |
