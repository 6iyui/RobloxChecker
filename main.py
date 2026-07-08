#!/usr/bin/env python3
"""
Simple Roblox Username Checker for Railway
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Reads usernames from words.txt, checks availability, IMMEDIATELY sends webhook for available ones.
Full Railway logging with timestamps and all events.
"""

import asyncio
import random
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import aiohttp

# ── Constants ────────────────────────────────────────────
ROBLOX_VALIDATE_URL = "https://auth.roblox.com/v1/usernames/validate"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
WEBHOOK_URL = "https://discord.com/api/webhooks/1521918363423473807/VN06dum7TWhC273Qt243Z-Ub6urM-aL2VwJmkPx6Gyx2fLVM_GrpEELvRcJryDkFraJH"
DELAY = 1  # Seconds between checks

def log(message, level="INFO"):
    """Log message with timestamp for Railway."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}", flush=True)

def random_birthday() -> str:
    """Generate a random birthday for Roblox API."""
    start = date(1990, 1, 1)
    end = date(2006, 12, 31)
    delta = (end - start).days
    return (start + timedelta(days=random.randint(0, delta))).isoformat()

async def send_webhook(session, username):
    """IMMEDIATELY send webhook notification for available username."""
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

    try:
        async with session.post(WEBHOOK_URL, json=embed, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 204:
                log(f"✅ WEBHOOK SENT IMMEDIATELY for '{username}'", "WEBHOOK")
                return True
            else:
                text = await resp.text()
                log(f"❌ Webhook failed for '{username}': HTTP {resp.status} - {text[:100]}", "WEBHOOK")
                return False
    except Exception as e:
        log(f"❌ Webhook error for '{username}': {e}", "WEBHOOK")
        return False

async def check_username(session, username):
    """Check if a username is available on Roblox."""
    payload = {"username": username, "context": "Signup", "birthday": random_birthday()}
    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Referer": "https://www.roblox.com/",
        "Origin": "https://www.roblox.com",
    }
    
    try:
        async with session.post(ROBLOX_VALIDATE_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                code = data.get("code")
                if code == 0:
                    log(f"✅ '{username}' is AVAILABLE!", "CHECK")
                    return True
                else:
                    log(f"❌ '{username}' is taken", "CHECK")
                    return False
            elif resp.status == 429:
                log(f"⚠️ Rate limited on '{username}', waiting 5s", "RATE")
                await asyncio.sleep(5)
                return None  # Retry later
            else:
                log(f"⚠️ HTTP {resp.status} for '{username}'", "ERROR")
                return False
    except Exception as e:
        log(f"⚠️ Exception for '{username}': {e}", "ERROR")
        return False

async def main():
    """Main function to check usernames from words.txt."""
    log("🚀 Roblox Username Checker starting...", "START")
    log(f"📁 Working directory: {Path.cwd()}", "INFO")
    
    # Check if words.txt exists
    wordlist_path = "words.txt"
    if not Path(wordlist_path).exists():
        log(f"❌ FATAL: '{wordlist_path}' not found in {Path.cwd()}", "FATAL")
        # List files for debugging
        try:
            files = [f for f in Path.cwd().iterdir() if f.is_file()]
            log(f"📁 Files in directory: {', '.join([f.name for f in files])}", "INFO")
        except:
            pass
        sys.exit(1)
    
    # Load usernames from words.txt
    try:
        with open(wordlist_path, 'r', encoding='utf-8') as f:
            usernames = [line.strip() for line in f if line.strip()]
    except Exception as e:
        log(f"❌ FATAL: Failed to read words.txt: {e}", "FATAL")
        sys.exit(1)
    
    # Remove duplicates
    original_count = len(usernames)
    usernames = list(dict.fromkeys(usernames))
    duplicates_removed = original_count - len(usernames)
    
    log(f"📄 Loaded {original_count} usernames from words.txt", "INFO")
    if duplicates_removed > 0:
        log(f"🗑️ Removed {duplicates_removed} duplicates, {len(usernames)} unique usernames", "INFO")
    
    log(f"🔍 Starting check of {len(usernames)} usernames...", "INFO")
    log(f"⚡ Webhook will be sent IMMEDIATELY when a username is found available!", "INFO")
    
    # Send startup webhook
    try:
        async with aiohttp.ClientSession() as session:
            startup_msg = {"content": "🟢 **Roblox Checker is Live and checking...**"}
            await session.post(WEBHOOK_URL, json=startup_msg, timeout=aiohttp.ClientTimeout(total=10))
            log("✅ Startup webhook sent", "WEBHOOK")
    except Exception as e:
        log(f"⚠️ Startup webhook failed: {e}", "WEBHOOK")
    
    # Check each username
    found = 0
    start_time = time.time()
    checked = 0
    rate_limited = 0
    errors = 0
    
    async with aiohttp.ClientSession() as session:
        for i, username in enumerate(usernames, 1):
            log(f"🔎 Checking {i}/{len(usernames)}: '{username}'", "PROGRESS")
            
            # Check username
            is_available = await check_username(session, username)
            checked += 1
            
            # IMMEDIATELY handle if available
            if is_available is True:
                log(f"🎯 FOUND AVAILABLE: '{username}' - SENDING WEBHOOK NOW!", "FOUND")
                if await send_webhook(session, username):
                    found += 1
                    log(f"✅ Webhook sent successfully for '{username}'", "SUCCESS")
                else:
                    log(f"❌ Failed to send webhook for '{username}'", "ERROR")
            elif is_available is False:
                pass  # Already logged in check_username
            elif is_available is None:
                rate_limited += 1
                # We already logged the rate limit
            
            # Log progress every 10 checks
            if i % 10 == 0:
                elapsed = time.time() - start_time
                rate = i / elapsed if elapsed > 0 else 0
                log(f"📊 Progress: {i}/{len(usernames)} checked ({rate:.1f}/s) | Found: {found} | Rate limited: {rate_limited}", "PROGRESS")
            
            # Delay between checks (shorter delay after finding one)
            if is_available is True:
                await asyncio.sleep(0.5)  # Short delay after finding
            else:
                await asyncio.sleep(DELAY)
    
    elapsed = time.time() - start_time
    log(f"🏁 FINISHED: Checked {len(usernames)} usernames in {elapsed:.2f} seconds", "COMPLETE")
    log(f"📊 Results: {found} available, {len(usernames) - found - rate_limited - errors} taken, {rate_limited} rate limited, {errors} errors", "COMPLETE")
    
    if found > 0:
        log(f"🎉 SUCCESS: {found} webhook(s) sent IMMEDIATELY upon finding available usernames!", "COMPLETE")
        # List all found usernames
        log(f"📝 Found usernames: Check Discord webhook for details", "COMPLETE")
    else:
        log("😔 No available usernames found this run", "COMPLETE")

if __name__ == "__main__":
    asyncio.run(main())
