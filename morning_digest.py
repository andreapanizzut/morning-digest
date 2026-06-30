#!/usr/bin/env python3
"""
morning_digest.py — Automated morning research agent.
Uses Ollama (gemma3) + DuckDuckGo Search to produce a daily digest.

MIT License
Copyright (c) 2025 Andrea Panizzut <andrea.panizzut@gmail.com>
https://www.andreapanizzut.it | https://www.linkedin.com/in/andreapanizzut/

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import argparse
import json
import logging
import os
import smtplib
import sys
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Logging — must be set up before any imports that might log
# ---------------------------------------------------------------------------
LOG_FILE = Path(__file__).parent / "digest.log"
_stdout_handler = logging.StreamHandler(sys.stdout)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        _stdout_handler,
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy imports with clear error messages
# ---------------------------------------------------------------------------
try:
    import ollama as ollama_lib
except ImportError:
    log.error("Library 'ollama' not found. Run: pip install ollama")
    sys.exit(1)

try:
    from ddgs import DDGS
except ImportError:
    log.error("Library 'ddgs' not found. Run: pip install ddgs")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants and defaults
# ---------------------------------------------------------------------------
KNOWN_PREF_FIELDS = {
    "ollama_host", "ollama_model",
    "email_sender", "email_password", "email_recipient",
    "smtp_host", "smtp_port",
    "topics", "exclude_sources", "preferred_sources",
    "languages", "num_articles", "tone", "extra_instructions",
    "digest_language",
}

DEFAULTS = {
    "ollama_host": "http://localhost:11434",
    "ollama_model": "gemma3:latest",
    "email_sender": "",
    "email_password": "",
    "email_recipient": "",
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "topics": [
        "AI policy", "artificial intelligence ethics",
        "philosophy of technology", "digital sociology",
        "quantum computing", "computer science research",
    ],
    "exclude_sources": [],
    "preferred_sources": [],
    "languages": ["en"],
    "num_articles": 8,
    "tone": "academic",
    "extra_instructions": "",
    "digest_language": "English",
}

SYSTEM_PROMPT_TEMPLATE = """\
You are an interdisciplinary researcher. You have received a list of recent articles and news found on the web.
Your task is to select the 6-8 most relevant ones at the intersection of: technology, computer science, AI, ethics, philosophy, and sociology.
Avoid purely commercial pieces or product announcements. Prefer: papers, analyses, op-eds, investigative journalism, academic threads.
For each selected article provide:
- Original title and source (with URL), in the article's original language
- 2-3 lines of summary in {digest_language} explaining why it is relevant
- A thematic tag from: [AI & Society] [Ethics] [Philosophy of Tech] [Digital Sociology] [Computer Science] [Emerging Tech]
At the end add a 'Common Thread' section of 4-5 lines in {digest_language} connecting the day's pieces into a coherent reflection."""

# ---------------------------------------------------------------------------
# Preferences loading
# ---------------------------------------------------------------------------

def load_env_config() -> dict:
    load_dotenv()
    cfg = {}
    mapping = {
        "OLLAMA_HOST": "ollama_host",
        "OLLAMA_MODEL": "ollama_model",
        "EMAIL_SENDER": "email_sender",
        "EMAIL_PASSWORD": "email_password",
        "EMAIL_RECIPIENT": "email_recipient",
        "SMTP_HOST": "smtp_host",
        "SMTP_PORT": "smtp_port",
        "DIGEST_LANGUAGE": "digest_language",
    }
    for env_key, cfg_key in mapping.items():
        val = os.environ.get(env_key)
        if val is not None:
            if cfg_key == "smtp_port":
                try:
                    cfg[cfg_key] = int(val)
                except ValueError:
                    log.warning("SMTP_PORT is not a valid integer: %s", val)
            else:
                cfg[cfg_key] = val
    return cfg


def load_json_prefs(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        log.error("Preferences file not found: %s", path)
        sys.exit(1)
    except json.JSONDecodeError as e:
        log.error("Invalid JSON in %s: %s", path, e)
        sys.exit(1)

    unknown = set(data.keys()) - KNOWN_PREF_FIELDS
    if unknown:
        log.warning("Unrecognized fields in preferences JSON: %s", ", ".join(sorted(unknown)))

    if "email_password" in data:
        log.warning(
            "WARNING: the preferences file contains 'email_password'. "
            "Do not commit this file to a public repository!"
        )

    return data


def build_config(prefs_path: str | None) -> dict:
    cfg = dict(DEFAULTS)
    env_cfg = load_env_config()
    cfg.update(env_cfg)
    if prefs_path:
        json_cfg = load_json_prefs(prefs_path)
        cfg.update(json_cfg)
    return cfg

# ---------------------------------------------------------------------------
# Web search
# ---------------------------------------------------------------------------

def search_articles(topics: list[str], days: int = 3, max_results_per_topic: int = 5) -> list[dict]:
    """Search recent articles on DuckDuckGo for each topic."""
    all_results = []
    seen_urls = set()

    clean_topics = [t.strip() for t in topics if t and t.strip()]
    if not clean_topics:
        log.warning("No valid topics found — using defaults.")
        clean_topics = DEFAULTS["topics"]

    ddgs = DDGS()
    for topic in clean_topics:
        log.info("Searching: '%s' (last %d days)", topic, days)
        try:
            results = list(ddgs.text(
                topic,
                timelimit=f"d{days}",
                max_results=max_results_per_topic,
            ))
            for r in results:
                url = r.get("href", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append({
                        "title": r.get("title", ""),
                        "url": url,
                        "snippet": r.get("body", ""),
                        "topic": topic,
                    })
            log.info("  [+] %d unique results for '%s'", len(results), topic)
        except Exception as e:
            log.warning("Search error for '%s': %s", topic, e)

    log.info("Total articles collected: %d", len(all_results))
    return all_results


def validate_urls(articles: list[dict], timeout: int = 6) -> list[dict]:
    """Drop articles whose URL does not respond with a 2xx or 3xx status code.
    Falls back to a GET request if HEAD is blocked (403/405), as some servers
    behind Cloudflare or paywalls reject HEAD unconditionally.
    """
    import urllib.request
    import urllib.error

    headers = {"User-Agent": "Mozilla/5.0 (compatible; MorningDigestBot/1.0)"}

    def check(url: str, method: str) -> int:
        req = urllib.request.Request(url, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status

    valid = []
    for a in articles:
        url = a["url"]
        try:
            status = check(url, "HEAD")
            if status < 400:
                valid.append(a)
            elif status in (403, 405):
                # Server blocked HEAD — retry with GET
                try:
                    status = check(url, "GET")
                    if status < 400:
                        log.info("  [ok-get] HEAD blocked, GET succeeded: %s", url)
                        valid.append(a)
                    else:
                        log.info("  [dead] HTTP %d (GET fallback): %s", status, url)
                except Exception:
                    log.info("  [dead] GET fallback also failed: %s", url)
            else:
                log.info("  [dead] HTTP %d: %s", status, url)
        except urllib.error.HTTPError as e:
            if e.code in (403, 405):
                # HEAD raised HTTPError — retry with GET
                try:
                    status = check(url, "GET")
                    if status < 400:
                        log.info("  [ok-get] HEAD blocked, GET succeeded: %s", url)
                        valid.append(a)
                    else:
                        log.info("  [dead] HTTP %d (GET fallback): %s", status, url)
                except Exception:
                    log.info("  [dead] GET fallback also failed: %s", url)
            else:
                log.info("  [dead] HTTP %d: %s", e.code, url)
        except Exception as e:
            log.info("  [dead] Unreachable (%s): %s", type(e).__name__, url)

    log.info("URL validation: %d/%d articles reachable", len(valid), len(articles))
    return valid

# ---------------------------------------------------------------------------
# Article archive (cross-run deduplication)
# ---------------------------------------------------------------------------

ARCHIVE_PATH = Path.home() / "morning_digest" / "articles_archive.json"


def load_archive(window_days: int = 15) -> dict:
    """Load into RAM only articles found within the last window_days days."""
    if not ARCHIVE_PATH.exists():
        return {}
    try:
        with open(ARCHIVE_PATH, encoding="utf-8") as f:
            full = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Archive corrupted or unreadable, starting fresh: %s", e)
        return {}
    cutoff = (datetime.now() - timedelta(days=window_days)).strftime("%Y-%m-%d")
    recent = {url: meta for url, meta in full.items() if meta.get("date_found", "") >= cutoff}
    log.info("Archive: %d total entries, %d within last %d days loaded into RAM", len(full), len(recent), window_days)
    return recent


def save_archive(archive: dict) -> None:
    ARCHIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ARCHIVE_PATH, "w", encoding="utf-8") as f:
        json.dump(archive, f, ensure_ascii=False, indent=2)
    log.info("Archive updated: %d total entries saved to %s", len(archive), ARCHIVE_PATH)


def filter_new_articles(articles: list[dict], archive: dict) -> tuple[list[dict], int]:
    """Remove articles already present in the archive. Returns (new_articles, n_skipped)."""
    new = []
    skipped = 0
    for a in articles:
        if a["url"] in archive:
            log.info("  [skip] Already seen on %s: %s", archive[a["url"]]["date_found"], a["title"][:60])
            skipped += 1
        else:
            new.append(a)
    return new, skipped


def add_to_archive(articles: list[dict], archive: dict) -> dict:
    """Add new articles to the archive with today's date."""
    today = datetime.now().strftime("%Y-%m-%d")
    for a in articles:
        archive[a["url"]] = {
            "title": a["title"],
            "date_found": today,
            "topic": a["topic"],
        }
    return archive

# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def build_context_block(articles: list[dict]) -> str:
    lines = ["## Articles found on the web (last 3 days)\n"]
    for i, a in enumerate(articles, 1):
        lines.append(f"### [{i}] {a['title']}")
        lines.append(f"**URL:** {a['url']}")
        lines.append(f"**Search topic:** {a['topic']}")
        lines.append(f"**Snippet:** {a['snippet']}")
        lines.append("")
    return "\n".join(lines)


def build_prefs_block(cfg: dict) -> str:
    parts = []
    if cfg.get("topics"):
        parts.append(f"- Topics of interest: {', '.join(cfg['topics'])}")
    if cfg.get("exclude_sources"):
        parts.append(f"- Exclude sources: {', '.join(cfg['exclude_sources'])}")
    if cfg.get("preferred_sources"):
        parts.append(f"- Preferred sources: {', '.join(cfg['preferred_sources'])}")
    if cfg.get("tone"):
        parts.append(f"- Digest tone: {cfg['tone']}")
    if cfg.get("num_articles"):
        parts.append(f"- Number of articles to include: {cfg['num_articles']}")
    if cfg.get("languages"):
        parts.append(f"- Accepted source languages: {', '.join(cfg['languages'])}")
    if cfg.get("extra_instructions"):
        parts.append(f"- Additional instruction: {cfg['extra_instructions']}")

    if not parts:
        return ""
    return "## Specific preferences for today:\n" + "\n".join(parts)


def build_full_prompt(articles: list[dict], cfg: dict) -> str:
    context = build_context_block(articles)
    prefs = build_prefs_block(cfg)

    user_content = context
    if prefs:
        user_content += "\n\n" + prefs

    digest_lang = cfg.get("digest_language", "English")
    user_content += (
        "\n\n---\n"
        f"Now produce the digest in Markdown format, following the system prompt instructions. "
        f"Keep article titles and URLs in their original language. "
        f"Write all summaries, tags, and the Common Thread section in {digest_lang}."
    )
    return user_content

# ---------------------------------------------------------------------------
# Ollama call
# ---------------------------------------------------------------------------

def call_ollama(cfg: dict, user_prompt: str) -> str:
    host = cfg["ollama_host"]
    model = cfg["ollama_model"]
    digest_lang = cfg.get("digest_language", "English")
    log.info("Connecting to Ollama: %s, model: %s", host, model)

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(digest_language=digest_lang)

    client = ollama_lib.Client(host=host)
    response = client.chat(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        options={"temperature": 0.7},
    )
    content = response["message"]["content"]
    log.info("Ollama response received (%d characters)", len(content))
    return content

# ---------------------------------------------------------------------------
# Markdown save
# ---------------------------------------------------------------------------

def save_markdown(digest: str, cfg: dict) -> Path:
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    output_dir = Path.home() / "morning_digest"
    output_dir.mkdir(parents=True, exist_ok=True)

    header = (
        f"# Morning Digest - {date_str}\n\n"
        f"*Generated on {now.strftime('%Y-%m-%d at %H:%M:%S')} "
        f"by {cfg['ollama_model']} via Ollama*\n\n---\n\n"
    )
    full_content = header + digest

    out_path = output_dir / f"digest_{date_str}.md"
    out_path.write_text(full_content, encoding="utf-8")
    log.info("Digest saved: %s", out_path)
    return out_path

# ---------------------------------------------------------------------------
# Markdown to HTML (minimal, no extra dependencies)
# ---------------------------------------------------------------------------

def md_to_html(md: str) -> str:
    import re

    lines = md.split("\n")
    html_lines = []
    in_list = False

    for line in lines:
        if line.startswith("### "):
            if in_list:
                html_lines.append("</ul>"); in_list = False
            html_lines.append(f"<h3>{line[4:].strip()}</h3>")
        elif line.startswith("## "):
            if in_list:
                html_lines.append("</ul>"); in_list = False
            html_lines.append(f"<h2>{line[3:].strip()}</h2>")
        elif line.startswith("# "):
            if in_list:
                html_lines.append("</ul>"); in_list = False
            html_lines.append(f"<h1>{line[2:].strip()}</h1>")
        elif line.strip() == "---":
            if in_list:
                html_lines.append("</ul>"); in_list = False
            html_lines.append("<hr>")
        elif line.startswith("- "):
            if not in_list:
                html_lines.append("<ul>"); in_list = True
            html_lines.append(f"<li>{line[2:].strip()}</li>")
        elif line.strip() == "":
            if in_list:
                html_lines.append("</ul>"); in_list = False
            html_lines.append("")
        else:
            if in_list:
                html_lines.append("</ul>"); in_list = False
            html_lines.append(f"<p>{line}</p>")

    if in_list:
        html_lines.append("</ul>")

    body = "\n".join(html_lines)
    body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", body)
    body = re.sub(r"\*(.+?)\*", r"<em>\1</em>", body)
    body = re.sub(r"`(.+?)`", r"<code>\1</code>", body)
    body = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', body)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Morning Digest</title>
</head>
<body style="font-family: Georgia, serif; max-width: 680px; margin: 0 auto; padding: 20px; color: #222; line-height: 1.7;">
<style>
  h1 {{ font-size: 1.6em; border-bottom: 2px solid #333; padding-bottom: 6px; }}
  h2 {{ font-size: 1.3em; margin-top: 2em; color: #444; }}
  h3 {{ font-size: 1.1em; margin-top: 1.5em; color: #111; }}
  hr {{ border: none; border-top: 1px solid #ccc; margin: 1.5em 0; }}
  a {{ color: #1a5276; }}
  code {{ background: #f4f4f4; padding: 2px 5px; border-radius: 3px; font-size: 0.9em; }}
  ul {{ padding-left: 1.4em; }}
  li {{ margin-bottom: 4px; }}
  p {{ margin: 0.6em 0; }}
  em {{ color: #555; font-size: 0.9em; }}
</style>
{body}
<br><hr>
<p style="font-size: 0.8em; color: #999;">
  Generated by morning_digest.py &middot; Ollama + DuckDuckGo Search
</p>
</body>
</html>"""

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def send_email(subject: str, html_body: str, cfg: dict) -> None:
    sender = cfg.get("email_sender", "")
    password = cfg.get("email_password", "")
    recipient = cfg.get("email_recipient", "")
    smtp_host = cfg.get("smtp_host", "smtp.gmail.com")
    smtp_port = int(cfg.get("smtp_port", 587))

    if not all([sender, password, recipient]):
        log.warning("Email credentials missing — email not sent.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    log.info("Sending email to %s via %s:%d", recipient, smtp_host, smtp_port)
    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, recipient, msg.as_string())
    log.info("Email sent successfully.")


def send_error_email(error_msg: str, cfg: dict) -> None:
    date_str = datetime.now().strftime("%Y-%m-%d")
    subject = f"[Morning Digest] Error - {date_str}"
    html = f"""<!DOCTYPE html><html><body style="font-family: sans-serif; max-width: 600px; margin: auto; padding: 20px;">
<h2 style="color: #c0392b;">Error generating digest</h2>
<p><strong>Date:</strong> {date_str}</p>
<pre style="background:#f8f8f8; padding:12px; border-radius:4px; font-size:0.9em;">{error_msg}</pre>
<p style="color:#999; font-size:0.8em;">morning_digest.py</p>
</body></html>"""
    try:
        send_email(subject, html, cfg)
    except Exception as e:
        log.error("Could not send error email either: %s", e)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Morning Digest — automated research agent using Ollama + DuckDuckGo"
    )
    parser.add_argument(
        "--prefs",
        metavar="PATH",
        help="Path to a JSON file with user preferences",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run the full pipeline but print the digest to stdout instead of saving or sending email. "
             "The article archive is NOT updated.",
    )
    args = parser.parse_args()

    cfg = build_config(args.prefs)
    dry_run = args.dry_run

    log.info("=== Morning Digest started%s ===", " [DRY RUN]" if dry_run else "")
    log.info("Ollama host: %s | Model: %s | Digest language: %s",
             cfg["ollama_host"], cfg["ollama_model"], cfg.get("digest_language", "English"))

    date_str = datetime.now().strftime("%Y-%m-%d")
    subject = f"Morning Digest - {date_str}"

    try:
        # 1. Load article archive (last 15 days only)
        archive = load_archive()
        log.info("Archive loaded: %d recent articles", len(archive))

        # 2. Web search
        clean_topic_count = max(len([t for t in cfg["topics"] if t and t.strip()]), 1)
        articles = search_articles(
            topics=cfg["topics"],
            days=3,
            max_results_per_topic=max(3, 20 // clean_topic_count),
        )
        if not articles:
            raise RuntimeError("No articles found from web search.")

        # 3. Validate URLs — drop dead links
        articles = validate_urls(articles)
        if not articles:
            raise RuntimeError("No reachable articles found after URL validation.")

        # 4. Filter already-seen articles
        articles, skipped = filter_new_articles(articles, archive)
        log.info("New articles: %d | Skipped (already seen): %d", len(articles), skipped)
        if not articles:
            raise RuntimeError("All articles found have already been processed in a previous run.")

        # 5. Build prompt
        user_prompt = build_full_prompt(articles, cfg)
        log.info("Prompt built (%d characters)", len(user_prompt))

        # 6. Call Ollama
        digest_md = call_ollama(cfg, user_prompt)

        if dry_run:
            separator = "=" * 72
            print(f"\n{separator}")
            print(f"  DRY RUN — {date_str}")
            print(f"  {len(articles)} articles | model: {cfg['ollama_model']}")
            print(separator)
            print(digest_md)
            print(separator)
            log.info("Dry run complete — archive not updated, no email sent.")
            return

        # 7. Update archive (only after a successful Ollama response)
        archive = add_to_archive(articles, archive)
        save_archive(archive)

        # 8. Save Markdown
        md_path = save_markdown(digest_md, cfg)

        # 9. Send email
        html_body = md_to_html(digest_md)
        send_email(subject, html_body, cfg)

        log.info("=== Digest complete: %s ===", md_path)

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        log.error("Critical error: %s", e, exc_info=True)
        send_error_email(f"{e}\n\n{tb}", cfg)
        sys.exit(1)


if __name__ == "__main__":
    main()
