# Morning Digest

> **Your daily AI-powered research briefing — zero cloud cost, fully private.**

Morning Digest is a command-line agent that searches the web for recent articles on your chosen topics, summarises them with a local language model, and delivers a structured digest to your inbox every morning. It runs entirely on your own hardware: no API keys, no per-token billing, no data leaving your network.

---

## How it works

```
DuckDuckGo Search  →  article archive filter  →  Ollama (Gemma 3)  →  Markdown + Email
```

1. Searches DuckDuckGo for each of your topics (last 3 days).
2. Skips any URL already seen in the past 15 days (local JSON archive).
3. Sends the fresh articles to a local Ollama model as context.
4. The model selects the most relevant ones and writes a structured digest.
5. Saves the digest as `~/morning_digest/digest_YYYY-MM-DD.md`.
6. Sends it to your inbox as an HTML email.

---

## Requirements

| Requirement | Notes |
|---|---|
| Python 3.11+ | |
| [Ollama](https://ollama.com/) | Running locally or on a LAN server |
| A pulled Ollama model | Recommended: `gemma3:12b` |
| Internet access | For DuckDuckGo search |
| Gmail account | Optional — only needed for email delivery |

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/andreapanizzut/morning-digest.git
cd morning-digest

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # Linux / macOS
.venv\Scripts\activate           # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Pull the Ollama model
ollama pull gemma3:12b

# 5. Copy the environment template
cp .env.example .env
# Edit .env with your values
```

---

## Configuration

Settings are resolved in this order: **JSON file > `.env` > built-in defaults**.

### `.env` file

```env
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=gemma3:12b

EMAIL_SENDER=you@gmail.com
EMAIL_PASSWORD=xxxx xxxx xxxx xxxx
EMAIL_RECIPIENT=you@gmail.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587

DIGEST_LANGUAGE=English
```

### JSON preferences file

All fields are optional. Pass it with `--prefs path/to/prefs.json`.

```json
{
  "ollama_host": "http://192.168.1.10:11434",
  "ollama_model": "gemma3:12b",
  "email_sender": "you@gmail.com",
  "email_password": "xxxx xxxx xxxx xxxx",
  "email_recipient": "you@gmail.com",
  "topics": ["AI policy", "quantum computing", "digital ethics"],
  "exclude_sources": ["TechCrunch", "The Verge"],
  "preferred_sources": ["arXiv", "MIT Technology Review"],
  "languages": ["en", "fr"],
  "num_articles": 8,
  "tone": "academic",
  "digest_language": "English",
  "extra_instructions": "Prioritise papers with concrete ethical implications."
}
```

### All configuration fields

| Field | Default | Description |
|---|---|---|
| `ollama_host` | `http://localhost:11434` | Ollama server URL |
| `ollama_model` | `gemma3:latest` | Model name as shown by `ollama list` |
| `email_sender` | — | Address used to send the email |
| `email_password` | — | Gmail app password (see below) |
| `email_recipient` | — | Delivery address |
| `smtp_host` | `smtp.gmail.com` | SMTP server hostname |
| `smtp_port` | `587` | SMTP port |
| `topics` | AI, ethics, philosophy… | Search queries sent to DuckDuckGo |
| `languages` | `["en"]` | Accepted source languages (passed to the model as a hint) |
| `digest_language` | `English` | Language for summaries and the Common Thread section |
| `num_articles` | `8` | Target number of articles in the digest |
| `tone` | `academic` | Writing tone (e.g. `academic`, `journalistic`, `casual`) |
| `exclude_sources` | `[]` | Sources the model should ignore |
| `preferred_sources` | `[]` | Sources the model should favour |
| `extra_instructions` | — | Free-text instruction appended to the prompt |

> **Security:** never commit a file containing `email_password` to a public repository.

---

## Usage

```bash
# Standard run
python morning_digest.py

# Run with a preferences file
python morning_digest.py --prefs prefs.json

# Dry run — prints the digest to the terminal, no email sent, archive not updated
python morning_digest.py --prefs prefs.json --dry-run
```

Use `--dry-run` to verify your setup before scheduling, or any time you want to preview a digest without triggering the full delivery pipeline.

---

## Gmail setup

Gmail requires an **app password** — your regular account password will not work with SMTP.

1. Enable **2-Step Verification** on your Google account.
2. Go to *Google Account → Security → App passwords*.
3. Create a new app password (select *Mail* and your device type).
4. Copy the 16-character string — spaces included are fine — into `email_password`.

---

## Output files

All output is written to `~/morning_digest/` (your home directory):

| File | Description |
|---|---|
| `digest_YYYY-MM-DD.md` | Full Markdown digest for the day |
| `articles_archive.json` | Persistent URL archive for deduplication |

The archive stores the full history on disk. On each run, only the last 15 days are loaded into memory for comparison.

---

## Scheduling

### Linux / macOS — cron

```bash
crontab -e
```

```cron
0 7 * * * /path/to/.venv/bin/python /path/to/morning_digest.py --prefs /path/to/prefs.json >> /path/to/digest.log 2>&1
```

### Windows — Task Scheduler

1. Open **Task Scheduler** → *Create Basic Task…*
2. **Trigger:** Daily, 07:00
3. **Action:** Start a program
   - **Program:** `C:\path\to\.venv\Scripts\python.exe`
   - **Arguments:** `C:\path\to\morning_digest.py --prefs C:\path\to\prefs.json`
   - **Start in:** `C:\path\to\morning_digest`
4. *General* tab → check *Run whether user is logged on or not*

To test immediately: right-click the task → *Run*.

---

## Ollama on a separate machine (LAN)

By default Ollama binds only to `127.0.0.1`. To expose it on your local network:

```bash
# Set before starting Ollama
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

Then set `ollama_host` in your preferences to `http://192.168.1.X:11434`.

---

## Project structure

```
morning-digest/
├── morning_digest.py        # main script
├── .env.example             # environment variable template
├── requirements.txt         # Python dependencies
├── LICENSE                  # MIT License
├── README.md                # this file
└── WHITEPAPER.md            # technical design document

~/morning_digest/            # runtime output (user home directory)
├── digest_YYYY-MM-DD.md
├── articles_archive.json
└── digest.log
```

---

## Third-party licenses

| Library | License | Author / Source |
|---|---|---|
| [ollama](https://github.com/ollama/ollama-python) | MIT | Ollama, Inc. |
| [ddgs](https://github.com/deedy5/ddgs) | MIT | deedy5 |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | BSD-3-Clause | Saurabh Kumar |

Python standard library modules (`argparse`, `smtplib`, `json`, `logging`, etc.) are covered by the [Python Software Foundation License](https://docs.python.org/3/license.html).

---

## License

This project is released under the [MIT License](LICENSE).

Copyright (c) 2025 Andrea Panizzut

---

## Author

**Andrea Panizzut**
- Website: [andreapanizzut.it](https://www.andreapanizzut.it)
- LinkedIn: [linkedin.com/in/andreapanizzut](https://www.linkedin.com/in/andreapanizzut/)
- Email: [andrea.panizzut@gmail.com](mailto:andrea.panizzut@gmail.com)
