#!/usr/bin/env python3
"""
Test script for DDS eBilling portal automation.
Run this tomorrow with real credentials and an open invoice.

Usage:
    python test_portal.py

You'll be prompted for credentials. Browser runs in VISIBLE mode
so you can watch and guide the automation.
"""
from app.automation.dds_ebilling import DDSeBillingBot
import getpass

def main():
    print("=" * 60)
    print("DDS eBilling Portal Test")
    print("=" * 60)
    print("\nThis will run the automation in VISIBLE mode.")
    print("Watch the browser and note any issues.\n")

    # Get credentials
    username = input("eBilling Username: ")
    password = getpass.getpass("eBilling Password: ")
    provider_name = input("Provider Name (as shown in portal): ")

    print("\n" + "-" * 60)
    print("Starting automation...")
    print("-" * 60 + "\n")

    # Run with visible browser
    bot = DDSeBillingBot(username, password, headless=False)

    try:
        bot.start()

        # Step 1: Login
        print("\n[STEP 1] Logging in...")
        if not bot.login():
            print("LOGIN FAILED - Check credentials or selectors")
            input("Press Enter to continue anyway...")

        # Step 2: Select provider
        print(f"\n[STEP 2] Selecting provider: {provider_name}")
        if not bot.select_provider(provider_name):
            print("PROVIDER SELECTION FAILED - Check provider name")
            input("Press Enter to continue anyway...")

        # Step 3: Navigate to invoices
        print("\n[STEP 3] Navigating to Invoices tab...")
        if not bot.navigate_to_invoice_entry():
            print("NAVIGATION FAILED - Check tab selector")
            input("Press Enter to continue anyway...")

        # Step 4: Search invoices
        print("\n[STEP 4] Searching for invoices (blank search)...")
        if not bot.search_invoices():
            print("SEARCH FAILED - Check search button selector")
            input("Press Enter to continue anyway...")

        # Step 5: Manual inspection point
        print("\n" + "=" * 60)
        print("INVOICE LIST SHOULD BE VISIBLE NOW")
        print("=" * 60)
        print("\nLook at the browser and note:")
        print("  - What does the Edit button look like?")
        print("  - How are invoices labeled?")
        print("  - What text is on the buttons?")

        input("\nPress Enter to try clicking Edit on first invoice...")

        # Step 6: Try clicking Edit
        print("\n[STEP 6] Clicking Edit on first invoice...")
        if not bot.click_invoice_edit():
            print("EDIT CLICK FAILED - Note the actual button label")
            input("Press Enter to continue anyway...")

        print("\n" + "=" * 60)
        print("INSIDE INVOICE - CHECK CLIENT/SESSION LIST")
        print("=" * 60)
        print("\nLook at the browser and note:")
        print("  - How are clients listed?")
        print("  - What link opens the calendar?")

        input("\nPress Enter to try opening calendar...")

        # Step 7: Try clicking sessions
        print("\n[STEP 7] Clicking sessions input...")
        if not bot.click_sessions_input():
            print("SESSION CLICK FAILED - Note the actual link text")
            input("Press Enter to continue anyway...")

        print("\n" + "=" * 60)
        print("CALENDAR VIEW - CHECK DATE INPUT FIELDS")
        print("=" * 60)
        print("\nLook at the browser and note:")
        print("  - How are dates displayed?")
        print("  - Where are the input fields?")
        print("  - What does the Submit button say?")

        input("\nPress Enter when done inspecting (will close browser)...")

    except Exception as e:
        print(f"\nERROR: {str(e)}")
        input("Press Enter to close browser...")

    finally:
        bot.stop()

    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)
    print("\nNotes for fixing selectors:")
    print("  - Edit automation file: app/automation/dds_ebilling.py")
    print("  - Update selectors based on what you observed")
    print("  - Run this test again to verify fixes")


if __name__ == "__main__":
    main()
