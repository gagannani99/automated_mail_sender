# Email Automation — Job Application Sender

A pure-Python terminal application that sends a personalised job-application
email (with your resume attached) to every HR contact listed in a PDF, via
your Gmail account — with conservative, human-like pacing, resumable
progress, and two independent layers of duplicate protection.

No AI, no LLMs, no web scraping, no external APIs — only two placeholders
(`{HR_NAME}` and `{COMPANY_NAME}`) are replaced in a fixed email template.

---

## Table of Contents

1. [Folder Structure](#1-folder-structure)
2. [Installation](#2-installation)
3. [How Gmail App Password Works](#3-how-gmail-app-password-works)
4. [How To Configure .env](#4-how-to-configure-env)
5. [Configuration Reference (config.py)](#5-configuration-reference-configpy)
6. [Running The App](#6-running-the-app)
7. [How Progress Resume Works](#7-how-progress-resume-works)
8. [How Duplicate Protection Works](#8-how-duplicate-protection-works)
9. [How Daily Limit Works](#9-how-daily-limit-works)
10. [How To Resume Tomorrow](#10-how-to-resume-tomorrow)
11. [Troubleshooting / Common Errors](#11-troubleshooting--common-errors)
12. [FAQ](#12-faq)

---

## 1. Folder Structure

```
email_automation/
├── main.py              # Entry point — coordinates the workflow only
├── parser.py              # PDF -> DataFrame contact extraction & cleaning
├── sender.py                # Gmail SMTP connection, message building, pacing
├── logger.py                  # File logging, CSV send-log, colored console output
├── config.py                    # All configurable constants + config validation
├── utils.py                       # Every reusable helper (single source of truth)
├── progress.json                    # Auto-managed, resumable send progress
├── requirements.txt
├── README.md
├── .env.example
├── data/
│   └── CompanyWise HR contact.pdf     # <-- put your HR contacts PDF here
├── attachments/
│   └── Resume.pdf                       # <-- your resume
└── logs/
    ├── sent_emails.csv                    # Full audit trail of every attempt
    └── application.log                      # App run log (lifecycle events, errors)
```

**Module responsibilities, at a glance:**

| File | Responsibility |
|---|---|
| `main.py` | Orchestrates the workflow only — no business logic lives here |
| `config.py` | Every tunable constant, plus `validate_config()` |
| `utils.py` | Email validation, time formatting, countdowns, progress & CSV file I/O — used by every other module, never duplicated |
| `parser.py` | Extracts, cleans, validates, and deduplicates HR contacts from the PDF |
| `sender.py` | SMTP connection, retries, message construction, human-like delays/breaks |
| `logger.py` | `application.log`, the CSV send-log wrapper, and colored terminal output |

## 2. Installation

```bash
cd email_automation
pip install -r requirements.txt
```

Requires **Python 3.10+** (the codebase uses modern type-hint syntax such
as `int | None`, which requires 3.10 or later).

Then place your input files:

- HR contacts PDF → `data/CompanyWise HR contact.pdf`
  (must contain a table with Name, Email, Title/Designation, and Company
  columns — a Serial Number column is optional).
- Resume → `attachments/Resume.pdf`

## 3. How Gmail App Password Works

Gmail will not accept your normal account password from a script like this
one — you need a dedicated **App Password**:

1. Turn on **2-Step Verification** on your Google account, if it isn't
   already on: <https://myaccount.google.com/security>
2. Go to <https://myaccount.google.com/apppasswords> and generate a new
   App Password (choose "Other" as the app name, e.g. "Email Automation").
3. Google gives you a 16-character password (spaces don't matter). Use
   *that* — not your real Gmail password — as `APP_PASSWORD`.
4. App Passwords can be revoked independently at any time from the same
   page, without changing your main Google password.

## 4. How To Configure .env

```bash
cp .env.example .env
```

Edit `.env`:

```
EMAIL=your.email@gmail.com
APP_PASSWORD=your16charapppassword
```

The app validates both values before attempting to connect: if either is
missing it prints `Missing EMAIL in .env` or `Missing APP_PASSWORD in .env`
and exits — it will never attempt to connect with partial credentials.

`.env` is only ever read locally by `python-dotenv`; it is never logged,
printed, or transmitted anywhere except to Gmail's own SMTP server for
authentication.

## 5. Configuration Reference (config.py)

| Constant | Default | Meaning |
|---|---|---|
| `EMAIL_SUBJECT` / `EMAIL_BODY_TEMPLATE` | — | The fixed subject/body; only `{HR_NAME}` and `{COMPANY_NAME}` are replaced |
| `DAILY_LIMIT` | `120` | Maximum emails sent per calendar day |
| `START_ROW` | `None` | `None` = auto-resume from `progress.json`; an integer forces sending to start from that row |
| `MIN_DELAY` / `MAX_DELAY` | `30` / `90` | Random delay range (seconds) between emails |
| `BREAK_AFTER` | `20` | Take a long break after this many successful sends |
| `BREAK_MINUTES_MIN` / `BREAK_MINUTES_MAX` | `5` / `10` | Long-break duration range (minutes) |
| `MAX_RETRIES` / `RETRY_DELAY_SECONDS` | `3` / `15` | Retry behaviour for transient SMTP/network errors |
| `SMTP_SERVER` / `SMTP_PORT` | `smtp.gmail.com` / `465` | Gmail SMTP over SSL |

Every one of these is validated by `config.validate_config()` at startup —
an invalid value (e.g. a negative `DAILY_LIMIT`, or `MIN_DELAY > MAX_DELAY`)
prints every problem found and exits before any file is touched or any
connection is attempted.

## 6. Running The App

```bash
python main.py
```

On every run, the app:

1. Validates `config.py` and your `.env` credentials.
2. Confirms the resume and HR PDF exist (creating `logs/` and
   `progress.json` automatically if they're missing).
3. Parses and cleans the HR contact list, showing exactly how many
   contacts were found, deduplicated, and marked invalid.
4. Asks **"Send Emails? (Y/N)"** before doing anything irreversible.
5. Shows a one-time preview of the first email about to be sent (subject,
   recipient, company, HR name, and a short body preview) and asks
   **"Continue? (Y/N)"**.
6. Sends emails one at a time, with a live dashboard refreshed before
   every send, randomized delays, and periodic longer breaks.
7. Stops automatically at the daily limit, saving progress immediately.

## 6a. Stopping The Project

Press **Ctrl+C** at any point — during a countdown, mid-send, or
anywhere else. The app catches the interrupt, immediately saves
progress, disconnects cleanly from Gmail, and prints exactly which row
was last completed and which row to resume from next:

```
=====================================
Program Interrupted
Progress Saved
Last Successful Row
157
Next Start Row
158
=====================================
```

No email is ever left half-sent — progress is only marked successful
*after* Gmail confirms the send.

## 7. How Progress Resume Works

`progress.json` stores exactly three fields:

```json
{
  "last_successful_row": 157,
  "emails_sent_today": 43,
  "last_run_date": "2026-08-01"
}
```

- **`last_successful_row`** is updated the instant an email is
  successfully sent — not batched, not delayed to the end of the run.
- On the next run, sending auto-resumes from `last_successful_row + 1`.
- **`emails_sent_today`** resets to `0` automatically the first time the
  app runs on a new calendar day (compared against `last_run_date`).
- Setting `START_ROW` in `config.py` to an integer overrides this
  entirely and starts from that row instead, ignoring `progress.json`'s
  saved position (useful for re-sending a specific range).
- If `progress.json` is deleted or corrupted, the app recreates it with
  safe defaults rather than crashing — you'd simply resume from row 1,
  though the CSV log (see below) still prevents actual re-sends.

## 8. How Duplicate Protection Works

Two **independent** safety mechanisms are used together:

1. **`progress.json`** — tracks *where* sending left off (the row
   pointer), so a normal resume doesn't re-scan contacts already passed.
2. **`logs/sent_emails.csv`** — the *authoritative* record of every
   email ever actually sent. Before sending to any contact, the app
   checks whether that email address already has a `Status=SENT` row in
   this CSV. If so, it's skipped with `Already Sent — Skipping`,
   **even if `progress.json` was deleted or reset.**

Within a single parse, duplicate email addresses in the PDF itself are
also removed (keeping the first occurrence), and reported as
"Duplicate Emails Removed" in the startup summary.

## 9. How Daily Limit Works

- `config.DAILY_LIMIT` (default `120`) is checked before every send.
- The moment `emails_sent_today` reaches the limit, the app immediately
  saves progress, disconnects from Gmail, prints:

  ```
  =====================================
  Daily Sending Limit Reached
  Emails Sent Today : 120
  Progress Saved Successfully
  Resume Tomorrow
  =====================================
  ```

  and exits cleanly — no partial or in-progress email is ever left
  hanging.
- The counter resets automatically the next calendar day; you don't
  need to do anything except re-run the app.

## 10. How To Resume Tomorrow

Just run it again:

```bash
python main.py
```

Because `emails_sent_today` resets on a new day and `last_successful_row`
already reflects everyone who's been sent to, the app will pick up
exactly where it left off — no manual bookkeeping required.

## 11. Troubleshooting / Common Errors

| Message | Cause | Fix |
|---|---|---|
| `Invalid configuration detected.` | A value in `config.py` is out of range (e.g. `MIN_DELAY > MAX_DELAY`) | Fix the listed value(s) in `config.py` |
| `Missing EMAIL in .env` / `Missing APP_PASSWORD in .env` | `.env` doesn't exist or is incomplete | `cp .env.example .env` and fill it in |
| `SMTP Authentication Failed` | Wrong App Password, or using your real Gmail password | Generate a new App Password and confirm 2-Step Verification is on |
| `Resume PDF not found` / `HR contacts PDF not found` | Files aren't at the expected paths | Place them at `attachments/Resume.pdf` and `data/CompanyWise HR contact.pdf` |
| `HR contacts PDF is empty (0 bytes)` | The PDF file has no content | Re-export/re-download the PDF |
| `No tables could be extracted from the PDF` | The PDF is a scanned image or plain-text layout, not a real table | Re-export as a proper table, or OCR it first |
| `No valid contact rows could be extracted from the PDF` | Table exists but no recognisable Name/Email/Company columns | Check the PDF's column headers |
| `Network Timeout` / `Connection Reset By Server` / `Connection Aborted` / `SMTP Connection Dropped` | Transient network issue | The app automatically retries (up to `MAX_RETRIES`); if it still fails, check your internet connection |
| `A required file could not be accessed.` (PermissionError) | OS-level file permission issue | Check that the app has write access to `logs/`, `progress.json`, and read access to the PDF/resume |
| `Could not write to CSV log ... the file may be open in another program` (warning, not fatal) | `logs/sent_emails.csv` is open in Excel or another program | Close the file — the app retries automatically and simply logs a warning if it still can't write; **it keeps sending, it does not stop** |
| `Could not save progress.json ... the file may be open in another program` (warning, not fatal) | `progress.json` is open/locked elsewhere | Close the file — the app retries automatically and continues the run; `sent_emails.csv` still prevents any duplicate sends in the meantime |

## 12. FAQ

**Is my Gmail account guaranteed to be safe from rate limiting?**
No. This app implements conservative, best-effort human-like pacing
(randomized delays, periodic breaks, a daily cap), but it cannot and does
not guarantee Gmail won't flag or throttle your account — that decision
is entirely Google's. Raise `DAILY_LIMIT`/lower the delays at your own risk.

**Can I safely stop the app mid-run?**
Yes — press `Ctrl+C`. Progress is saved after every single successful
send (not just at the end), and the app prints exactly which row to
resume from next.

**What if I delete `progress.json` by accident?**
The app recreates it with safe defaults automatically. You won't
double-send, because `logs/sent_emails.csv` independently tracks every
address already marked `SENT`.

**Can I re-send to a specific row range?**
Set `START_ROW` in `config.py` to the row number you want to start from.
Note that contacts already marked `SENT` in the CSV log will still be
skipped even with a manual `START_ROW`.

**Is this compliant with anti-spam regulations?**
Make sure every recipient is someone you have a legitimate reason to
contact (e.g. publicly listed HR/recruiting contacts), and that your
outreach complies with applicable regulations (e.g. CAN-SPAM) in your
jurisdiction. The email template always includes your real name and
contact details. Honour any removal/opt-out requests you receive.