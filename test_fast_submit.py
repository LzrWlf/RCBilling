#!/usr/bin/env python3
"""
Test harness for submit_to_ebilling_fast().
Tests submitting Kim Austin (UCI 2719815) via HTTP.

Usage:
    source venv/bin/activate && python test_fast_submit.py
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
    print("FAST SUBMISSION TEST")
    print("=" * 60)

    creds = get_credentials_from_db()
    if not creds:
        print("ERROR: No credentials found")
        sys.exit(1)

    print(f"Login: {creds['name']} ({creds['regional_center']})")
    print(f"Portal: {creds['portal_url']}")

    # Parse the real CSV file instead of hardcoding
    from app.csv_parser import parse_rc_billing_csv, records_to_dict

    csv_path = '/Users/timothyramos/Desktop/OA_MM_DD_YYYY_Num.CSV'
    print(f"\nParsing CSV: {csv_path}")
    records_obj = parse_rc_billing_csv(csv_path)
    records = records_to_dict(records_obj)

    if not records:
        print("ERROR: No records found in CSV")
        sys.exit(1)

    test_record = records[0]
    print(f"\nTest record: {test_record['consumer_name']} (UCI: {test_record['uci']})")
    print(f"  SPN: {test_record['spn_id']}, Month: {test_record['service_month']}")
    print(f"  Days: {test_record['service_days']}, Units: {test_record['entered_units']}")

    from app.automation.dds_ebilling import submit_to_ebilling_fast

    print("\n[TEST] Running submit_to_ebilling_fast()...")
    start = time.time()
    results = submit_to_ebilling_fast(
        records=[test_record],
        username=creds['username'],
        password=creds['password'],
        provider_name='PP0212',
        regional_center=creds['regional_center'],
        portal_url=creds['portal_url'],
    )
    elapsed = time.time() - start

    print("\n" + "=" * 60)
    print(f"TIME: {elapsed:.1f} seconds")
    print(f"RESULTS: {len(results)} record(s)")

    for i, r in enumerate(results):
        print(f"\n--- Result {i+1} ---")
        print(f"  Consumer: {r.consumer_name}")
        print(f"  UCI: {r.uci}")
        print(f"  Success: {r.success}")
        print(f"  Partial: {r.partial}")
        print(f"  Days entered: {r.days_entered}")
        print(f"  Days expected: {r.days_expected}")
        print(f"  Unavailable days: {r.unavailable_days}")
        print(f"  Already entered days: {r.already_entered_days}")
        print(f"  Error: {r.error_message}")
        print(f"  RC Units Billed: {r.rc_units_billed}")
        print(f"  RC Gross: {r.rc_gross_amount}")
        print(f"  RC Net: {r.rc_net_amount}")
        print(f"  RC Unit Rate: {r.rc_unit_rate}")

    # Summary
    success_count = sum(1 for r in results if r.success)
    partial_count = sum(1 for r in results if r.partial)
    already_count = sum(1 for r in results if r.already_entered_days)
    failed_count = len(results) - success_count - partial_count

    print("\n" + "=" * 60)
    print(f"SUMMARY: {success_count} success, {partial_count} partial, {failed_count} failed")
    if already_count:
        print(f"  ({already_count} had already-entered days)")

    if success_count > 0 or any(r.already_entered_days for r in results):
        print("\nRESULT: PASS")
    else:
        print("\nRESULT: FAIL")
        for r in results:
            if r.error_message:
                print(f"  ERROR: {r.error_message}")


if __name__ == '__main__':
    main()
