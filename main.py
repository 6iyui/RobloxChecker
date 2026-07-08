#!/usr/bin/env python3
"""
Roblox Username Checker with Rich Live Dashboard
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Interactive menu-driven username checker with:
- Live status table (recent checks)
- Live stats panel (taken/available/errors/rate-limited)
- Live log panel (events like rate limits, CSRF renewals)
- Webhook notifications (with proper error handling/retries)
- Proxy support
- Dictionary mode – check words from a list file
"""

import asyncio
import itertools
import random
import string
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from collections import deque

import aiohttp
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt, FloatPrompt, Confirm
from rich.live import Live
from rich.layout import Layout
from rich import box
from rich.text import Text

console = Console()

# ── Constants ────────────────────────────────────────────
ROBLOX_VALIDATE_URL = "https://auth.roblox.com/v1/usernames/validate"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
DEFAULT_DELAY = 1
DEFAULT_CONCURRENCY = 7
MAX_RETRIES = 3
WEBHOOK_URL = "https://discord.com/api/webhooks/1521918363423473807/VN06dum7TWhC273Qt243Z-Ub6urM-aL2VwJmkPx6Gyx2fLVM_GrpEELvRcJryDkFraJH"
WEBHOOK_MAX_RETRIES = 3

# ── Character sets ───────────────────────────────────────
LETTERS = string.ascii_lowercase
DIGITS = string.digits
ALPHANUM = LETTERS + DIGITS

CHAR_MAP = {
    "l": LETTERS,
    "d": DIGITS,
    "c": ALPHANUM,
}

# ── Live state (shared across tasks) ─────────────────────
available_found = []
recent_checks = deque(maxlen=10)   # (username, status, details)
live_logs = deque(maxlen=8)        # log messages
stats = {
    "taken": 0, "available": 0, "errors": 0, "rate_limited": 0,
    "total_checked": 0, "total": 0, "start_time": None,
    "active_proxies": 0, "total_proxies": 0,
    "csrf_token": "",
    "webhook_sent": 0, "webhook_failed": 0,
}

# ── Helpers ──────────────────────────────────────────────
def parse_pattern(pattern: str) -> list[tuple[int, str]]:
    segments = []
    for part in pattern.split("+"):
        part = part.strip()
        if not part:
            continue
        count = int(part[:-1])
        char_type = part[-1]
        if char_type not in CHAR_MAP:
            raise ValueError(f"Unknown character type '{char_type}'. Use l, d, c.")
        segments.append((count, char_type))
    return segments

def generate_random_username(segs: list[tuple[int, str]]) -> str:
    username = ""
    for cnt, ctype in segs:
        username += "".join(random.choice(CHAR_MAP[ctype]) for _ in range(cnt))
    return username

def generate_usernames(pattern: str, count: int | None = None) -> list[str]:
    segs = parse_pattern(pattern)
    total = 1
    for cnt, ctype in segs:
        total *= len(CHAR_MAP[ctype]) ** cnt
    if count is None or count >= total:
        char_sets = [itertools.product(CHAR_MAP[ct], repeat=cnt) for cnt, ct in segs]
        all_combos = itertools.product(*char_sets)
        usernames = ["".join("".join(seg) for seg in combo) for combo in all_combos]
        random.shuffle(usernames)
        return usernames
    else:
        sample = set()
        while len(sample) < count:
            sample.add(generate_random_username(segs))
        usernames = list(sample)
        random.shuffle(usernames)
        return usernames

def load_proxies(filepath: str) -> list[str]:
    proxies = []
    if not filepath or not Path(filepath).exists():
        return proxies
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                proxies.append(line)
    return proxies

def random_birthday() -> str:
    start = date(1990, 1, 1)
    end = date(2006, 12, 31)
    delta = (end - start).days
    return (start + timedelta(days=random.randint(0, delta))).isoformat()

def clean_proxy_display(proxy: str | None) -> str:
    if not proxy:
        return "Direct"
    # Remove scheme and auth for display
    p = proxy.replace("http://", "").replace("https://", "").replace("socks5://", "")
    if "@" in p:
        p = p.split("@")[-1]
    return p

# ── Webhook ──────────────────────────────────────────────
async def send_startup_webhook(webhook_url: str):
    """Sends a one-off 'checker is live' notification when the script starts."""
    if not webhook_url:
        return
    payload = {"content": "🟢 **Roblox Checker is Live and checking...**"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 204:
                    text = await resp.text()
                    print(f"[WEBHOOK] Startup ping failed: HTTP {resp.status} - {text[:200]}", flush=True)
    except Exception as e:
        print(f"[WEBHOOK] Startup ping exception: {e}", flush=True)

async def send_webhook(session: aiohttp.ClientSession, webhook_url: str, username: str):
    """Sends an 'available username' notification to Discord.
    Properly checks the response status, retries on Discord rate limits,
    and logs failures instead of silently swallowing them.
    """
    if not webhook_url:
        live_logs.append("⚠️ Webhook URL not set, skipping notification")
        return

    embed = {
        "embeds": [{
            "title": "🟢 Available Roblox Username!",
            "description": f"**`{username}`** is available!",
            "color": 0x57F287,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "Roblox Checker"},
            "fields": [
                {"name": "Username", "value": f"`{username}`", "inline": True},
                {"name": "Length", "value": str(len(username)), "inline": True},
            ],
        }]
    }

    for attempt in range(WEBHOOK_MAX_RETRIES):
        try:
            async with session.post(
                webhook_url, json=embed, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 204:
                    stats["webhook_sent"] += 1
                    return  # success
                elif resp.status == 429:
                    try:
                        body = await resp.json()
                        retry_after = float(body.get("retry_after", 1))
                    except Exception:
                        retry_after = 1.0
                    live_logs.append(f"⚠️ Webhook rate limited, retrying in {retry_after:.1f}s")
                    print(f"[WEBHOOK] Rate limited notifying '{username}', retrying in {retry_after:.1f}s", flush=True)
                    await asyncio.sleep(retry_after)
                    continue
                else:
                    text = await resp.text()
                    stats["webhook_failed"] += 1
                    live_logs.append(f"❌ Webhook failed ({resp.status}) for '{username}'")
                    print(f"[WEBHOOK] Failed for '{username}': HTTP {resp.status} - {text[:200]}", flush=True)
                    return
        except Exception as e:
            stats["webhook_failed"] += 1
            live_logs.append(f"❌ Webhook error for '{username}': {str(e)[:50]}")
            print(f"[WEBHOOK] Exception notifying '{username}': {e}", flush=True)
            return

    # Exhausted retries (kept getting 429s)
    stats["webhook_failed"] += 1
    live_logs.append(f"❌ Webhook gave up on '{username}' after {WEBHOOK_MAX_RETRIES} retries")
    print(f"[WEBHOOK] Gave up notifying '{username}' after {WEBHOOK_MAX_RETRIES} retries", flush=True)

# ── CSRF ─────────────────────────────────────────────────
async def fetch_csrf_token(session: aiohttp.ClientSession, proxy: str | None = None) -> str:
    headers = {
        "User-Agent": USER_AGENT, "Content-Type": "application/json",
        "Accept": "application/json", "Referer": "https://www.roblox.com/",
        "Origin": "https://www.roblox.com",
    }
    payload = {"username": "a", "context": "Signup", "birthday": "1990-01-01"}
    try:
        async with session.post(ROBLOX_VALIDATE_URL, json=payload, headers=headers,
                                proxy=proxy, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            token = resp.headers.get("x-csrf-token")
            if token:
                return token
    except:
        pass
    return ""

# ── Check one username ───────────────────────────────────
async def check_username(session: aiohttp.ClientSession, username: str,
                         csrf_token: str, proxy: str | None = None) -> dict:
    payload = {"username": username, "context": "Signup", "birthday": random_birthday()}
    headers = {
        "User-Agent": USER_AGENT, "Content-Type": "application/json",
        "Accept": "application/json", "Referer": "https://www.roblox.com/",
        "Origin": "https://www.roblox.com", "X-CSRF-TOKEN": csrf_token,
    }
    try:
        async with session.post(ROBLOX_VALIDATE_URL, json=payload, headers=headers,
                                proxy=proxy, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                code = data.get("code")
                if code == 0:
                    return {"status": "available", "username": username}
                else:
                    return {"status": "taken", "username": username}
            elif resp.status == 403:
                new_token = resp.headers.get("x-csrf-token")
                if new_token:
                    return {"status": "retry_csrf", "username": username, "new_token": new_token}
                return {"status": "error", "username": username, "detail": "403 Forbidden"}
            elif resp.status == 429:
                retry_after = float(resp.headers.get("Retry-After", 5))
                return {"status": "rate_limited", "username": username, "retry_after": retry_after}
            else:
                return {"status": "error", "username": username, "detail": f"HTTP {resp.status}"}
    except Exception as e:
        return {"status": "error", "username": username, "detail": str(e)[:50]}

# ── Worker ───────────────────────────────────────────────
async def worker(queue: asyncio.Queue, session: aiohttp.ClientSession,
                 proxy: str | None, delay: float, sem: asyncio.Semaphore,
                 csrf_token_ref: list, output_file: str):
    proxy_display = clean_proxy_display(proxy)
    
    while not queue.empty():
        username = await queue.get()
        async with sem:
            csrf_token = csrf_token_ref[0]
            result = await check_username(session, username, csrf_token, proxy)

            # CSRF renewal
            if result["status"] == "retry_csrf":
                new_token = result.get("new_token")
                if new_token:
                    csrf_token_ref[0] = new_token
                    stats["csrf_token"] = new_token[:25] + "..."
                    live_logs.append(f"🔄 CSRF token renewed")
                    result = await check_username(session, username, new_token, proxy)

            # Update stats
            if result["status"] == "available":
                stats["available"] += 1
                available_found.append(username)
                with open(output_file, "a") as f:
                    f.write(username + "\n")
                await send_webhook(session, WEBHOOK_URL, username)
                recent_checks.append((username, "AVAILABLE ✅", proxy_display))
                live_logs.append(f"🟢 FOUND AVAILABLE: '{username}'!")
                print(f"[CHECK] {username} -> AVAILABLE", flush=True)
            elif result["status"] == "taken":
                stats["taken"] += 1
                recent_checks.append((username, "TAKEN ❌", proxy_display))
                print(f"[CHECK] {username} -> taken", flush=True)
            elif result["status"] == "rate_limited":
                stats["rate_limited"] += 1
                retry_after = result.get("retry_after", 5)
                recent_checks.append((username, "RATE LIMITED 🟠", proxy_display))
                live_logs.append(f"⚠️ Rate limited on '{username}'. Waiting {retry_after:.0f}s")
                print(f"[CHECK] {username} -> rate_limited (waiting {retry_after:.0f}s)", flush=True)
                await asyncio.sleep(retry_after)
                retries = result.get("_retries", 0)
                if retries < MAX_RETRIES:
                    result["_retries"] = retries + 1
                    await queue.put(username)
            else:
                stats["errors"] += 1
                detail = result.get("detail", "Unknown")
                recent_checks.append((username, f"ERROR ⚠️", proxy_display))
                live_logs.append(f"❌ Error on '{username}': {detail}")
                print(f"[CHECK] {username} -> error ({detail})", flush=True)

            stats["total_checked"] += 1
            print(f"[PROGRESS] {stats['total_checked']}/{stats['total']} checked so far", flush=True)
            if result["status"] != "rate_limited":
                await asyncio.sleep(delay)
        queue.task_done()

# ── Live dashboard layout ────────────────────────────────
def make_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="progress", size=3),
        Layout(name="main"),
        Layout(name="footer", size=3),
    )
    layout["main"].split_row(
        Layout(name="recent", ratio=2),
        Layout(name="side"),
    )
    layout["side"].split_column(
        Layout(name="stats"),
        Layout(name="logs"),
    )
    return layout

def render_dashboard(layout: Layout):
    # Progress bar
    total = stats["total"]
    checked = stats["total_checked"]
    pct = (checked / total * 100) if total > 0 else 0
    bar_width = 50
    filled = int(bar_width * checked / total) if total > 0 else 0
    bar = "█" * filled + "╺" * (bar_width - filled)
    elapsed = time.time() - (stats["start_time"] or time.time())
    elapsed_str = time.strftime("%H:%M:%S", time.gmtime(elapsed))
    if checked > 0 and total > 0:
        eta_seconds = (elapsed / checked) * (total - checked)
        eta_str = time.strftime("%H:%M:%S", time.gmtime(eta_seconds))
    else:
        eta_str = "-:--:--"
    progress_text = f"  {bar}  {checked}/{total}  •  {elapsed_str}  •  {eta_str}"
    layout["progress"].update(Panel(progress_text, title="Scan Progress", border_style="cyan"))

    # Recent checks table
    table = Table(box=box.SIMPLE, padding=(0, 1))
    table.add_column("#", style="dim", width=4)
    table.add_column("Username", style="bold")
    table.add_column("Status")
    table.add_column("Proxy/IP", style="dim")
    start_idx = max(0, stats["total_checked"] - len(recent_checks))
    for i, (user, status, proxy) in enumerate(recent_checks, start=start_idx):
        color = "green" if "AVAILABLE" in status else ("orange1" if "RATE" in status else ("yellow" if "ERROR" in status else "red"))
        table.add_row(str(i), user, f"[{color}]{status}[/]", proxy)
    layout["recent"].update(Panel(table, title="Recent Checks", border_style="cyan"))

    # Stats panel
    available = stats["available"]
    taken = stats["taken"]
    errors = stats["errors"]
    rate_limited = stats["rate_limited"]
    stats_text = f"""
  [green]Available:     {available:>6}[/]
  [red]Taken:         {taken:>6}[/]
  [yellow]Errors:        {errors:>6}[/]
  [orange1]Rate Limited:  {rate_limited:>6}[/]
  ─────────────────────
  [cyan]Total Checked: {stats['total_checked']:>6}[/]
  [dim]Total:         {total:>6}[/]

  [dim]Proxies: {stats['active_proxies']}/{stats['total_proxies']} active[/]
  [dim]CSRF: {stats['csrf_token'] or 'N/A'}[/]
  [dim]Webhook: {stats['webhook_sent']} sent / {stats['webhook_failed']} failed[/]
    """
    layout["stats"].update(Panel(stats_text.strip(), title="📊 Stats", border_style="green"))

    # Live log
    log_lines = "\n".join(f"  {msg}" for msg in live_logs)
    layout["logs"].update(Panel(log_lines or "  [dim]Waiting for events...[/]", title="📜 Live Log", border_style="yellow"))

# ── Main check runner ────────────────────────────────────
async def run_checks(usernames: list[str], concurrency: int, delay: float,
                     proxy_file: str | None, output_file: str):
    global available_found
    available_found = []
    recent_checks.clear()
    live_logs.clear()
    stats.update({
        "taken": 0, "available": 0, "errors": 0, "rate_limited": 0,
        "total_checked": 0, "total": len(usernames), "start_time": time.time(),
        "active_proxies": 0, "total_proxies": 0, "csrf_token": "",
        "webhook_sent": 0, "webhook_failed": 0,
    })

    with open(output_file, "w") as f:
        f.write("")

    proxies = load_proxies(proxy_file) if proxy_file else []
    if not proxies:
        proxies = [None]
    stats["total_proxies"] = len(proxies)
    stats["active_proxies"] = len([p for p in proxies if p is not None])
    proxy_iter = itertools.cycle(proxies)

    queue = asyncio.Queue()
    for u in usernames:
        queue.put_nowait(u)

    sem = asyncio.Semaphore(concurrency)
    cookie_jar = aiohttp.CookieJar()

    layout = make_layout()
    is_tty = console.is_terminal
    live = Live(layout, refresh_per_second=4, console=console) if is_tty else None
    if live:
        live.start()
    else:
        print("[SETUP] Non-interactive environment detected (e.g. Railway) — using plain-text logs instead of the live dashboard.", flush=True)

    async def update_dashboard():
        while True:
            render_dashboard(layout)
            await asyncio.sleep(0.25)

    dashboard_task = asyncio.create_task(update_dashboard()) if is_tty else None

    async with aiohttp.ClientSession(cookie_jar=cookie_jar) as session:
        live_logs.append("🔑 Fetching CSRF token...")
        print("[SETUP] Fetching CSRF token...", flush=True)
        csrf_token = await fetch_csrf_token(session, next(proxy_iter))
        if not csrf_token:
            live_logs.append("❌ Failed to obtain CSRF token.")
            stats["csrf_token"] = "FAILED"
            print("[SETUP] Failed to obtain CSRF token.", flush=True)
        else:
            stats["csrf_token"] = csrf_token[:25] + "..."
            live_logs.append(f"✅ CSRF token obtained")
            print("[SETUP] CSRF token obtained.", flush=True)
        csrf_token_ref = [csrf_token or ""]

        workers = []
        for _ in range(concurrency):
            proxy = next(proxy_iter)
            task = asyncio.create_task(
                worker(queue, session, proxy, delay, sem, csrf_token_ref, output_file)
            )
            workers.append(task)

        live_logs.append(f"🚀 Started {concurrency} workers with {delay}s delay")
        print(f"[SETUP] Started {concurrency} workers with {delay}s delay. Checking {len(usernames)} usernames total.", flush=True)
        await queue.join()
        for w in workers:
            w.cancel()

    if dashboard_task:
        dashboard_task.cancel()
    if live:
        live.stop()

    console.print(f"\n  [bold green]✅ Done![/]")
    console.print(f"  Available: [green]{stats['available']}[/] | Taken: [red]{stats['taken']}[/] | Errors: [yellow]{stats['errors']}[/] | Rate-limited: [orange1]{stats['rate_limited']}[/]")
    console.print(f"  Webhook: [cyan]{stats['webhook_sent']} sent[/] / [red]{stats['webhook_failed']} failed[/]")
    if stats['available'] > 0:
        console.print(f"  [bold cyan]Available usernames saved to {output_file}[/]")

# ── Menu functions ──────────────────────────────────────

def show_banner():
    banner = """
[bold cyan]
╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║         🎮  Roblox Username Checker  •  v2.0  🎮             ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
[/]
"""
    console.print(banner)

def menu_generate_and_check():
    console.print("\n  [bold cyan]─── Generate & Check ───[/]\n")
    console.print("  Pattern format: segments separated by '+'")
    console.print("    [dim]l = letters, d = digits, c = alphanumeric[/]")
    console.print("    [dim]Example: 4l = 4 letters, 3l+1d = 3 letters + 1 digit[/]\n")

    pattern = Prompt.ask("  ▶  Enter pattern", default="4l")
    
    try:
        segs = parse_pattern(pattern)
    except ValueError as e:
        console.print(f"  [red]❌ {e}[/]")
        return

    total = 1
    for cnt, ctype in segs:
        total *= len(CHAR_MAP[ctype]) ** cnt
    
    console.print(f"  Total possible combinations: [cyan]{total:,}[/]")

    if total <= 100000:
        choice = Prompt.ask("  ▶  Generate all or random sample?", choices=["all", "random"], default="random")
    else:
        console.print("  [yellow]⚠️  Too many combinations for 'all' mode. Using random sample.[/]")
        choice = "random"

    if choice == "all":
        count = None
    else:
        count = IntPrompt.ask("  ▶  How many random usernames?", default=500)

    concurrency = IntPrompt.ask("  ▶  Concurrency (workers)", default=DEFAULT_CONCURRENCY)
    delay = FloatPrompt.ask("  ▶  Delay between checks (seconds)", default=DEFAULT_DELAY)

    use_proxies = Confirm.ask("  ▶  Use proxies?", default=False)
    proxy_file = None
    if use_proxies:
        proxy_file = Prompt.ask("  ▶  Proxy file path", default="proxies_fast.txt")
        if not Path(proxy_file).exists():
            console.print(f"  [yellow]⚠️  {proxy_file} not found. Running without proxies.[/]")
            proxy_file = None

    output_file = Prompt.ask("  ▶  Output file", default="available_roblox.txt")

    console.print(f"\n  [dim]Generating {count or 'all'} usernames...[/]")
    usernames = generate_usernames(pattern, count)
    console.print(f"  📋 Generated [cyan]{len(usernames):,}[/] usernames. Starting check...\n")

    asyncio.run(run_checks(usernames, concurrency, delay, proxy_file, output_file))

def menu_dictionary_check():
    console.print("\n  [bold cyan]─── Dictionary Check ───[/]\n")
    console.print("  Provide a text file with one username per line.")
    console.print("  [dim]Example: words_alpha.txt, common_passwords.txt, etc.[/]\n")

    wordlist_path = Prompt.ask("  ▶  Wordlist file path", default="words_alpha.txt")
    if not Path(wordlist_path).exists():
        console.print(f"  [red]❌ File '{wordlist_path}' not found.[/]")
        return

    # Load words
    try:
        with open(wordlist_path, 'r', encoding='utf-8') as f:
            usernames = [line.strip() for line in f if line.strip()]
    except Exception as e:
        console.print(f"  [red]❌ Failed to read file: {e}[/]")
        return

    console.print(f"  Loaded [cyan]{len(usernames):,}[/] words from '{wordlist_path}'.")

    concurrency = IntPrompt.ask("  ▶  Concurrency (workers)", default=DEFAULT_CONCURRENCY)
    delay = FloatPrompt.ask("  ▶  Delay between checks (seconds)", default=DEFAULT_DELAY)

    use_proxies = Confirm.ask("  ▶  Use proxies?", default=False)
    proxy_file = None
    if use_proxies:
        proxy_file = Prompt.ask("  ▶  Proxy file path", default="proxies_fast.txt")
        if not Path(proxy_file).exists():
            console.print(f"  [yellow]⚠️  {proxy_file} not found. Running without proxies.[/]")
            proxy_file = None

    output_file = Prompt.ask("  ▶  Output file", default="available_roblox.txt")

    console.print(f"\n  [dim]Starting dictionary check...[/]\n")
    asyncio.run(run_checks(usernames, concurrency, delay, proxy_file, output_file))

def run_dictionary_default(wordlist_path: str = "words.txt"):
    """Runs Dictionary Check immediately with default settings, no prompts."""
    console.print("\n  [bold cyan]─── Dictionary Check (auto) ───[/]\n")

    if not Path(wordlist_path).exists():
        console.print(f"  [red]❌ File '{wordlist_path}' not found.[/]")
        return

    try:
        with open(wordlist_path, 'r', encoding='utf-8') as f:
            usernames = [line.strip() for line in f if line.strip()]
    except Exception as e:
        console.print(f"  [red]❌ Failed to read file: {e}[/]")
        return

    console.print(f"  Loaded [cyan]{len(usernames):,}[/] words from '{wordlist_path}'.")

    concurrency = DEFAULT_CONCURRENCY
    delay = DEFAULT_DELAY
    proxy_file = None
    output_file = "available_roblox.txt"

    console.print(f"  Concurrency: [cyan]{concurrency}[/]  Delay: [cyan]{delay}s[/]  Output: [cyan]{output_file}[/]")
    console.print(f"\n  [dim]Starting dictionary check...[/]\n")
    asyncio.run(run_checks(usernames, concurrency, delay, proxy_file, output_file))

def menu_settings():
    console.print("\n  [bold cyan]─── Settings ───[/]\n")
    console.print(f"  Default delay: [cyan]{DEFAULT_DELAY}s[/]")
    console.print(f"  Default concurrency: [cyan]{DEFAULT_CONCURRENCY}[/]")
    console.print(f"  Webhook: [cyan]{'Enabled' if WEBHOOK_URL else 'Disabled'}[/]")
    console.print("\n  [dim](Edit the script to change defaults permanently)[/]")

def main():
    show_banner()
    asyncio.run(send_startup_webhook(WEBHOOK_URL))
    run_dictionary_default("words.txt")

if __name__ == "__main__":
    main()
