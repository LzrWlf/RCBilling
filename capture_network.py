#!/usr/bin/env python3
"""
Network traffic capture during an invoice scrape.
Logs all HTTP requests/responses to network_log.json so we can
identify which URLs to call directly with requests (skipping Playwright).

Usage:
    source venv/bin/activate && python capture_network.py
"""
import sys
import os
import json
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from app import create_app
from app.models import Provider
from playwright.sync_api import sync_playwright

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), 'screenshots')
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

# Collect network traffic
traffic_log = []
PORTAL_DOMAIN = 'ebilling.dds.ca.gov'

# Skip static assets
SKIP_EXTENSIONS = {'.css', '.js', '.png', '.jpg', '.jpeg', '.gif', '.ico', '.svg', '.woff', '.woff2', '.ttf'}


def should_log(url: str) -> bool:
    """Only log requests to the portal domain, skip static assets."""
    if PORTAL_DOMAIN not in url:
        return False
    for ext in SKIP_EXTENSIONS:
        if url.split('?')[0].endswith(ext):
            return False
    return True


def on_request(request):
    if not should_log(request.url):
        return
    entry = {
        'timestamp': datetime.now().isoformat(),
        'direction': 'REQUEST',
        'method': request.method,
        'url': request.url,
        'headers': dict(request.headers),
        'post_data': request.post_data,
    }
    traffic_log.append(entry)
    print(f"  >> {request.method} {request.url}")
    if request.post_data:
        print(f"     POST data: {request.post_data[:200]}")


def on_response(response):
    if not should_log(response.url):
        return
    try:
        body_size = len(response.body()) if response.status == 200 else 0
    except:
        body_size = -1

    # Capture response body for HTML pages (the ones we want to parse)
    body_preview = ''
    if response.status == 200:
        content_type = response.headers.get('content-type', '')
        if 'text/html' in content_type:
            try:
                body = response.text()
                body_preview = body[:500]
                body_size = len(body)
            except:
                pass

    entry = {
        'timestamp': datetime.now().isoformat(),
        'direction': 'RESPONSE',
        'status': response.status,
        'url': response.url,
        'headers': dict(response.headers),
        'body_size': body_size,
        'body_preview': body_preview,
    }
    traffic_log.append(entry)
    print(f"  << {response.status} {response.url} ({body_size} bytes)")


def get_credentials():
    app = create_app()
    with app.app_context():
        providers = Provider.query.filter_by(is_active=True).all()
        for provider in providers:
            if provider.regional_center != 'SGPRC':
                continue
            username, password = provider.get_credentials()
            if username and password:
                return {
                    'username': username,
                    'password': password,
                    'portal_url': provider.rc_portal_url,
                }
    return None


def main():
    print("=" * 60)
    print("NETWORK TRAFFIC CAPTURE")
    print("=" * 60)

    creds = get_credentials()
    if not creds:
        print("ERROR: No credentials found")
        sys.exit(1)

    print(f"Portal: {creds['portal_url']}")
    print(f"Username: {creds['username']}")
    print()

    # Import the bot and run a scrape with network listeners
    from app.automation.dds_ebilling import DDSeBillingBot

    with DDSeBillingBot(
        username=creds['username'],
        password=creds['password'],
        headless=False,
        regional_center='SGPRC',
        portal_url=creds['portal_url']
    ) as bot:
        # Login first (switches to popup window)
        print("[1] Logging in...")
        if not bot.login():
            print("ERROR: Login failed")
            sys.exit(1)

        # Attach network listeners AFTER login — bot.page now points to the active popup
        bot.page.on('request', on_request)
        bot.page.on('response', on_response)
        print("[*] Network listeners attached to active page")
        print(f"[*] Current URL: {bot.page.url}")
        print()

        # Capture cookies after login
        cookies = bot.context.cookies()
        print(f"[*] Session cookies ({len(cookies)}):")
        for c in cookies:
            print(f"    {c['name']}={c['value'][:20]}... (domain={c['domain']})")
        print()

        # Get providers
        providers = bot.get_available_providers()
        print(f"[2] Found {len(providers)} providers")
        print()

        # Scrape just the FIRST provider with invoices (PP0212 or PP1829)
        # to keep the capture focused
        for prov in providers:
            spn_id = prov['spn_id']
            if spn_id in ('HP0197', 'PP0508'):
                continue  # Skip empty providers

            print(f"[3] Scanning provider {spn_id}...")
            if not bot.select_provider(spn_id):
                continue

            if not bot.navigate_to_invoices():
                continue

            # Scrape search results
            search_results = bot.scrape_all_invoice_pages()
            print(f"    Found {len(search_results)} invoice rows")

            # Expand just the FIRST 2 invoices to capture the detail page traffic
            for inv in search_results[:2]:
                print(f"    Expanding invoice {inv['invoice_id']}...")
                folder_invoices = bot.expand_multi_consumer_folder(inv)
                print(f"    Got {len(folder_invoices)} consumers")
                bot.navigate_to_invoices()

            break  # Only scan one provider for traffic capture

        # Save cookies separately for testing
        cookies_file = os.path.join(os.path.dirname(__file__), 'captured_cookies.json')
        with open(cookies_file, 'w') as f:
            json.dump(cookies, f, indent=2)
        print(f"\n[*] Cookies saved to {cookies_file}")

    # Save traffic log
    log_file = os.path.join(os.path.dirname(__file__), 'network_log.json')
    with open(log_file, 'w') as f:
        json.dump(traffic_log, f, indent=2, default=str)

    print(f"\n[*] Traffic log saved to {log_file}")
    print(f"[*] Total entries: {len(traffic_log)}")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY OF UNIQUE URLs")
    print("=" * 60)
    urls = {}
    for entry in traffic_log:
        if entry['direction'] == 'REQUEST':
            key = f"{entry['method']} {entry['url'].split('?')[0]}"
            urls[key] = urls.get(key, 0) + 1

    for url, count in sorted(urls.items(), key=lambda x: -x[1]):
        print(f"  {count}x  {url}")


if __name__ == "__main__":
    main()
