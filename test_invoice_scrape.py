#!/usr/bin/env python3
"""
Non-interactive test harness for invoice scraping.
Designed to be run by the Ralph Wiggum loop (unattended).

Pulls credentials from the app's encrypted database.
Uses scrape_all_providers_inventory() to scan ALL providers on the login.
Exits 0 if invoices found, 1 if zero invoices or error.

Usage:
    source venv/bin/activate && python test_invoice_scrape.py
"""
import sys
import os
import logging

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


def get_credentials_from_db():
    """Pull credentials from the Flask app's encrypted Provider table.
    Prefers SGPRC (Wonderkind) since ELARC password is expired.
    """
    from app import create_app
    from app.models import Provider

    app = create_app()
    with app.app_context():
        providers = Provider.query.filter_by(is_active=True).all()

        # Prefer SGPRC provider
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

        # Fallback: any provider with credentials
        for provider in providers:
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
    print("INVOICE SCRAPE TEST HARNESS")
    print("=" * 60)

    creds = get_credentials_from_db()
    if not creds:
        print("ERROR: No provider with stored credentials found in database.")
        sys.exit(1)

    print(f"Login: {creds['name']} ({creds['regional_center']})")
    print(f"Portal URL: {creds['portal_url']}")
    print(f"Username: {creds['username']}")
    print(f"Mode: Scan ALL providers on this login")
    print(f"Headless: {os.environ.get('PLAYWRIGHT_HEADLESS', 'false')}")

    from app.automation.dds_ebilling import scrape_all_providers_inventory

    print("\n[TEST] Running scrape_all_providers_inventory()...")
    print("[TEST] This scans all 4 providers (HP0197, PP0212, PP0508, PP1829)")
    result = scrape_all_providers_inventory(
        username=creds['username'],
        password=creds['password'],
        regional_center=creds['regional_center'],
        portal_url=creds['portal_url'],
    )

    # Report results
    print("\n" + "=" * 60)
    print(f"STATUS: {result.get('status', 'unknown')}")

    if result.get('status') == 'error':
        print(f"ERROR: {result.get('error', 'unknown')}")
        print(f"MESSAGE: {result.get('message', '')}")
        print("INVOICE_COUNT: 0")
        print("RESULT: FAIL")
        sys.exit(1)

    # Show per-provider breakdown
    providers_scanned = result.get('providers_scanned', [])
    print(f"PROVIDERS_SCANNED: {len(providers_scanned)}")
    for p in providers_scanned:
        print(f"  {p.get('spn_id', '?')}: {p.get('name', '?')}")

    invoices = result.get('invoices', [])
    print(f"INVOICE_COUNT: {len(invoices)}")

    if invoices:
        print("RESULT: PASS")
        print(f"\nFirst 10 invoices:")
        for inv in invoices[:10]:
            print(f"  Invoice {inv.get('invoice_id', '?')}: "
                  f"SVC={inv.get('svc_code', '?')} "
                  f"Month={inv.get('service_month', inv.get('svc_month', '?'))} "
                  f"UCI={inv.get('uci', '(multi)')} "
                  f"Provider={inv.get('provider_spn', '?')}")
        if len(invoices) > 10:
            print(f"  ... and {len(invoices) - 10} more")
        sys.exit(0)
    else:
        print("RESULT: FAIL - Zero invoices returned across all providers")
        if result.get('warnings'):
            print(f"WARNINGS: {result['warnings']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
