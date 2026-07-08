#!/usr/bin/env python3
"""
Simple Roblox Username Checker for Railway
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Reads usernames from words.txt, checks availability, IMMEDIATELY sends webhook for available ones.
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
DELAY = 1.5  # Seconds between checks

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
                log(f"✅ WEBHOOK SENT for '{username}'", "WEBHOOK")
                return True
            else:
                text = await resp.text()
                log(f"❌ Webhook failed for '{username}': HTTP {resp.status}", "WEBHOOK")
                return False
    except Exception as e:
        log(f"❌ Webhook error for '{username}': {e}", "WEBHOOK")
        return False

async def check_username(session, username, retry_count=0):
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
        async with session.post(ROBLOX_VALIDATE_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                data = await resp.json()
                code = data.get("code")
                if code == 0:
                    return True  # Available
                else:
                    return False  # Taken
            elif resp.status == 429:
                log(f"Rate limited on '{username}', waiting 10s", "RATE")
                await asyncio.sleep(10)
                if retry_count < 3:
                    return await check_username(session, username, retry_count + 1)
                return False
            elif resp.status == 403:
                log(f"CSRF error on '{username}', waiting 5s", "ERROR")
                await asyncio.sleep(5)
                if retry_count < 2:
                    return await check_username(session, username, retry_count + 1)
                return False
            else:
                log(f"HTTP {resp.status} for '{username}'", "ERROR")
                if retry_count < 2:
                    await asyncio.sleep(2)
                    return await check_username(session, username, retry_count + 1)
                return False
    except asyncio.TimeoutError:
        log(f"Timeout for '{username}', retrying", "ERROR")
        if retry_count < 2:
            await asyncio.sleep(2)
            return await check_username(session, username, retry_count + 1)
        return False
    except Exception as e:
        log(f"Exception for '{username}': {e}", "ERROR")
        if retry_count < 2:
            await asyncio.sleep(2)
            return await check_username(session, username, retry_count + 1)
        return False

async def main():
    """Main function to check usernames from words.txt."""
    log("🚀 Roblox Username Checker starting...", "START")
    log(f"📁 Working directory: {Path.cwd()}", "INFO")
    
    # Check if words.txt exists
    wordlist_path = "words.txt"
    if not Path(wordlist_path).exists():
        log(f"❌ '{wordlist_path}' not found in {Path.cwd()}", "FATAL")
        # List files for debugging
        try:
            files = [f for f in Path.cwd().iterdir() if f.is_file()]
            log(f"Files in directory: {', '.join([f.name for f in files])}", "INFO")
        except:
            pass
        sys.exit(1)
    
    # Load usernames from words.txt
    try:
        with open(wordlist_path, 'r', encoding='utf-8') as f:
            usernames = [line.strip() for line in f if line.strip()]
    except Exception as e:
        log(f"❌ Failed to read words.txt: {e}", "FATAL")
        sys.exit(1)
    
    # Remove duplicates
    usernames = list(dict.fromkeys(usernames))
    
    log(f"📄 Loaded {len(usernames)} unique usernames from words.txt", "INFO")
    
    # Send startup webhook
    try:
        async with aiohttp.ClientSession() as session:
            startup_msg = {"content": "🟢 **Roblox Checker is Live and checking...**"}
            await session.post(WEBHOOK_URL, json=startup_msg, timeout=aiohttp.ClientTimeout(total=10))
            log("✅ Startup notification sent", "WEBHOOK")
    except Exception as e:
        log(f"⚠️ Startup notification failed: {e}", "WEBHOOK")
    
    # Check each username
    found = 0
    start_time = time.time()
    
    async with aiohttp.ClientSession() as session:
        for i, username in enumerate(usernames, 1):
            log(f"🔍 Checking {i}/{len(usernames)}: {username}", "PROGRESS")
            
            try:
                # Check username
                is_available = await check_username(session, username)
                
                if is_available:
                    log(f"🎯 FOUND AVAILABLE: '{username}'!", "FOUND")
                    # IMMEDIATELY send webhook
                    if await send_webhook(session, username):
                        found += 1
                        log(f"✅ Webhook sent for '{username}'", "SUCCESS")
                    else:
                        log(f"❌ Webhook failed for '{username}'", "ERROR")
                else:
                    log(f"❌ '{username}' is taken", "CHECK")
            except Exception as e:
                log(f"⚠️ Error checking '{username}': {e}", "ERROR")
            
            # Progress update every 10 checks
            if i % 10 == 0:
                elapsed = time.time() - start_time
                log(f"📊 Progress: {i}/{len(usernames)} checked, {found} found so far", "PROGRESS")
            
            # Delay between checks
            await asyncio.sleep(DELAY)
    
    elapsed = time.time() - start_time
    log(f"✅ FINISHED in {elapsed:.2f} seconds", "COMPLETE")
    log(f"📊 Found {found} available username(s)", "COMPLETE")
    
    if found > 0:
        log(f"🎉 {found} webhook(s) sent successfully!", "COMPLETE")
    else:
        log("😔 No available usernames found", "COMPLETE")

if __name__ == "__main__":
    asyncio.run(main())
