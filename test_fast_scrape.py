#!/usr/bin/env python3
"""
Test harness for the fast HTTP-based invoice scraper.
Compares speed and results against the expected 75 invoices.

Usage:
    source venv/bin/activate && python test_fast_scrape.py
"""
import sys
import os
import time
import logging

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

# Suppress SSL warnings for the portal's custom port
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def get_credentials_from_db():
    from app import create_app
    from app.models import Provider
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
                    'regional_center': provider.regional_center,
                    'portal_url': provider.rc_portal_url,
                    'name': provider.name,
                }
    return None


def main():
    print("=" * 60)
    print("FAST SCRAPER TEST")
    print("=" * 60)

    creds = get_credentials_from_db()
    if not creds:
        print("ERROR: No credentials found")
        sys.exit(1)

    print(f"Login: {creds['name']} ({creds['regional_center']})")
    print(f"Portal: {creds['portal_url']}")

    from app.automation.dds_ebilling import scrape_all_providers_inventory_fast

    print("\n[TEST] Running scrape_all_providers_inventory_fast()...")
    start = time.time()
    result = scrape_all_providers_inventory_fast(
        username=creds['username'],
        password=creds['password'],
        regional_center=creds['regional_center'],
        portal_url=creds['portal_url'],
    )
    elapsed = time.time() - start

    print("\n" + "=" * 60)
    print(f"STATUS: {result.get('status', 'unknown')}")
    print(f"TIME: {elapsed:.1f} seconds")

    if result.get('status') == 'error':
        print(f"ERROR: {result.get('error')}")
        print(f"MESSAGE: {result.get('message')}")
        sys.exit(1)

    providers_scanned = result.get('providers_scanned', [])
    print(f"PROVIDERS_SCANNED: {len(providers_scanned)}")
    for p in providers_scanned:
        print(f"  {p.get('spn_id', '?')}: {p.get('name', '?')}")

    invoices = result.get('invoices', [])
    print(f"INVOICE_COUNT: {len(invoices)}")

    # Show per-provider breakdown
    by_provider = {}
    for inv in invoices:
        spn = inv.get('provider_spn', '?')
        by_provider[spn] = by_provider.get(spn, 0) + 1
    print("\nPer-provider breakdown:")
    for spn, count in sorted(by_provider.items()):
        print(f"  {spn}: {count} invoices")

    # Show sample invoices with all fields
    print(f"\nSample invoices (first 5):")
    for inv in invoices[:5]:
        print(f"  Invoice {inv.get('invoice_id')}: "
              f"{inv.get('last_name')}, {inv.get('first_name')} | "
              f"UCI={inv.get('uci')} | "
              f"SVC={inv.get('svc_code')}/{inv.get('svc_subcode')} | "
              f"Auth={inv.get('auth_number')} | "
              f"Month={inv.get('service_month')} | "
              f"Provider={inv.get('provider_spn')}")

    if len(invoices) >= 75:
        print(f"\nRESULT: PASS ({len(invoices)} >= 75 expected)")
    else:
        print(f"\nRESULT: FAIL ({len(invoices)} < 75 expected)")
        sys.exit(1)


if __name__ == "__main__":
    main()
