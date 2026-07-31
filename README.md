# Email Automation — Job Application Sender

A pure-Python terminal application that sends a personalised job-application
email (with your resume attached) to every HR contact listed in a PDF,
via your Gmail account.

No AI, no LLMs, no web scraping, no external APIs — only two placeholders
(`{HR_NAME}` and `{COMPANY_NAME}`) are replaced in a fixed email template.

---

## 1. Project structure

```
email_automation/
├── main.py              # Entry point / workflow orchestration
├── parser.py             # PDF -> DataFrame contact extraction
├── sender.py              # Gmail SMTP connection + message building
├── logger.py               # App logging + CSV send-log
├── config.py                # All configurable constants
├── utils.py                  # Email validation, delays, small helpers
├── progress.json               # Auto-managed send progress (resumable)
├── requirements.txt
├── README.md
├── .env.example
├── data/
│   └── CompanyWise HR contact.pdf   # <-- put your HR contacts PDF here
├── attachments/
│   └── Resume.pdf                    # <-- your resume (already included)
└── logs/
    ├── sent_emails.csv                 # Full audit trail of every attempt
    └── application.log                  # App run log
```

## 2. Setup

### 2.1 Install dependencies

```bash
cd email_automation
pip install -r requirements.txt
```

### 2.2 Add your input files

- Place your HR contacts PDF at: `data/CompanyWise HR contact.pdf`
  (must contain a table with Name, Email, Title/Designation, and Company
  columns — a Serial Number column is optional).
- Your resume is already at `attachments/Resume.pdf`. Replace it if needed.

### 2.3 Configure Gmail credentials

1. Enable 2-Step Verification on your Google account.
2. Generate a Gmail **App Password**: https://myaccount.google.com/apppasswords
3. Copy the example env file and fill it in:

```bash
cp .env.example .env
```

```
EMAIL=your.email@gmail.com
APP_PASSWORD=your16charapppassword
```

**Never use your real Gmail password** — only an App Password works with
this script, and it can be revoked independently at any time.

## 3. Run

```bash
python main.py
```

The app will:

1. Verify the resume and HR PDF exist.
2. Parse and clean the HR contact list (trim, validate emails, dedupe).
3. Show you how many contacts are eligible to receive an email today.
4. Ask for explicit confirmation (`Y`/`N`) before sending anything.
5. Send emails one at a time with randomised delays between sends and
   periodic longer breaks, to avoid triggering Gmail's spam/rate protections.
6. Stop automatically once the daily limit (`DAILY_LIMIT` in `config.py`,
   default 80) is reached.
7. Save progress after every successful send, so you can safely stop and
   resume the app on a later day — already-sent contacts are always skipped.

## 4. Configuration

All tunable values live in `config.py`, including:

- `EMAIL_SUBJECT`, `EMAIL_BODY_TEMPLATE`
- `DAILY_LIMIT`, `MIN_DELAY` / `MAX_DELAY`, `BREAK_AFTER`,
  `BREAK_MINUTES_MIN` / `BREAK_MINUTES_MAX`
- `SMTP_SERVER`, `SMTP_PORT`
- File paths for logs, progress, PDF, and resume

## 5. Resuming / re-running

- `progress.json` tracks every email address that has already been sent
  to, plus how many were sent "today" (resets automatically each new day).
- `logs/sent_emails.csv` is a full audit trail (timestamp, contact, status,
  reason) of every attempt — sent, failed, or skipped — and is also used
  as a secondary check to avoid double-sending.
- Re-running `python main.py` will always skip anyone already marked as
  sent, so it's safe to stop (Ctrl+C) and resume at any time.

## 6. Important notes on sending at scale

- Gmail imposes its own daily sending limits for personal accounts
  (historically around 500 recipients/day) and will flag accounts that
  send too fast or in large bursts. The defaults here (80/day, 25–55s
  between sends, periodic breaks) are intentionally conservative —
  raise them at your own risk.
- Make sure every recipient on your list is someone you have a legitimate
  reason to contact (e.g. publicly listed HR/recruiting contacts) and
  that your outreach complies with applicable anti-spam regulations
  (e.g. CAN-SPAM) in your jurisdiction — always include your real name
  and contact details (already present in the template) and honour any
  removal/opt-out requests you receive.
- If Gmail temporarily blocks sending or asks for extra verification,
  stop the script, resolve the issue in your Google account, then re-run
  — progress will pick up exactly where it left off.

## 7. Troubleshooting

| Problem | Likely cause |
|---|---|
| `Gmail authentication failed` | Wrong/missing App Password, or 2-Step Verification not enabled |
| `HR contacts PDF not found` | File isn't at `data/CompanyWise HR contact.pdf` |
| `No tables could be extracted from the PDF` | PDF doesn't contain a real table (e.g. it's a scanned image) — try OCR first |
| Some rows missing after parsing | Check `logs/application.log` for which rows were dropped (empty/invalid/duplicate email) |
