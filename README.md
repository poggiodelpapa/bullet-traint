![Bullet Train't](btanew.png)

# 🚄 Bullet Train't

A lightweight GitHub Actions bot that monitors Trenitalia's availability for a specific route and sends a branded email notification the moment a ticket becomes purchasable.

Built because apparently getting from Venice to Rome by Frecciarossa is harder than it sounds.

---

## How it works

```
Every 8 hours → call Trenitalia API → parse results → filter by train type & time window
    ├── nothing available  → silent, do nothing
    └── ticket found       → send branded HTML email with price, times & direct link
```

1. **GitHub Actions** runs the script 3 times a day on a cron schedule (9:00, 15:00, 21:00 Italian time)
2. The script calls the **lefrecce.it internal API** directly — same endpoint the website uses
3. Results are filtered by **train type** (Frecciarossa only) and **departure window** (configurable)
4. Availability is determined by checking `saleable`, `bookable`, and `minprice > 0` on each result
5. If a purchasable ticket is found, a **mobile-friendly HTML email** is sent with times, duration, price, and a direct link to buy
6. The raw API response is **committed back to the repo** after every run for debugging

---

## Stack

| Tool | Role |
| --- | --- |
| Python 3.12 | Core logic |
| `requests` | API calls and session management |
| `smtplib` + Gmail App Password | Email delivery |
| GitHub Actions | Scheduling and execution |
| Git (via Actions) | Persistent response storage for debug |

---

## Features

- Calls the real Trenitalia API — no fragile HTML scraping
- Filters by train category (Frecciarossa), departure time window, and actual purchasability
- Mobile-first branded HTML email with card layout — readable at a glance on your phone
- Fully automated, no server or paid service required
- Runs within GitHub Actions free tier (uses ~6 min/day out of 2000 min/month)
- Credentials stored as GitHub Secrets, never in code
- Manual trigger with optional `force_email` flag for testing

---

## Setup

### 1. Clone or fork this repo

```
git clone https://github.com/poggiodelpapa/bullet-traint
```

### 2. Configure Gmail

1. Go to [myaccount.google.com](https://myaccount.google.com)
2. **Security** → **2-Step Verification** (must be enabled)
3. Search for **"App passwords"** → create one for "Mail"
4. Copy the 16-character password — paste it without spaces

### 3. Add GitHub Secrets

**Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Secret | Value |
| --- | --- |
| `EMAIL_MITTENTE` | Gmail address used to send |
| `EMAIL_DESTINATARIO` | Address to receive notifications |
| `GMAIL_APP_PASSWORD` | 16-character app password from step 2 |

### 4. Enable write permissions for Actions

**Settings** → **Actions** → **General** → **Workflow permissions** → select **"Read and write permissions"**

### 5. Run manually to test

**Actions** → **Monitor Frecciarossa Trenitalia** → **Run workflow** → set `force_email` to `true`

You'll receive a test email immediately. From that point on, real emails only fire when a ticket is actually available.

---

## Customizing the route

Edit the variables at the top of `monitor_trenitalia.py`:

```python
ORIGINE           = "VENEZIA MESTRE"
DESTINAZIONE      = "ROMA TERMINI"
DATA_VIAGGIO      = "30/07/2025"
ORA_DA            = 9    # departure window start (inclusive)
ORA_A             = 15   # departure window end (exclusive)
SOLO_FRECCIAROSSA = True
```

Station names must match Trenitalia's internal naming exactly (all caps, as they appear on tickets).

## Customizing the schedule

Edit the cron expressions in `monitor_trenitalia.yml`:

```yaml
- cron: "0 7 * * *"    # 09:00 Italian time (UTC+2 summer)
- cron: "0 13 * * *"   # 15:00 Italian time
- cron: "0 19 * * *"   # 21:00 Italian time
```

---

## Repo structure

```
bullet-traint/
├── monitor_trenitalia.py   ← main script
├── last_response.json      ← raw API response from last run (auto-updated)
├── btanew.png              ← logo used in email header
└── .github/
    └── workflows/
        └── monitor_trenitalia.yml  ← Actions workflow definition
```

---

## Example notification

> **Subject:** 🚄 Frecciarossa disponibile! VENEZIA MESTRE → ROMA TERMINI — 30/07/2025

The email shows a card for each available train with departure time, arrival time, journey duration, minimum price, and a direct button to lefrecce.it.

---

## Sister project

[**Sherlock Bands**](https://github.com/poggiodelpapa/sherlock-bands) — same idea, different target: watches a Sapienza University page for ranking updates and sends a diff email when something changes.
