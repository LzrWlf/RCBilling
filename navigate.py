#!/usr/bin/env python3
"""
Interactive navigation script for mapping DDS eBilling portal.
Run this and guide the navigation - I'll capture selectors as we go.
"""
from playwright.sync_api import sync_playwright
import time

PORTAL_URL = "https://ebilling.dds.ca.gov:8373/login"

def main():
    print("=" * 60)
    print("DDS eBilling Portal Navigator")
    print("=" * 60)
    print("\nLaunching browser...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # Visible browser
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        print(f"\nNavigating to: {PORTAL_URL}")
        page.goto(PORTAL_URL, wait_until="networkidle")

        print("\n" + "=" * 60)
        print("BROWSER IS OPEN - Portal login page should be visible")
        print("=" * 60)
        print("\nInstructions:")
        print("1. Enter your credentials in the browser")
        print("2. Navigate through the portal as you normally would")
        print("3. Come back here and press ENTER at each step")
        print("4. I'll capture the current URL and page info")
        print("\nPress ENTER after each navigation step...")
        print("Type 'quit' to exit\n")

        step = 1
        while True:
            user_input = input(f"Step {step} - Press ENTER (or 'quit'): ").strip().lower()

            if user_input == 'quit':
                print("\nClosing browser...")
                break

            # Capture current state
            current_url = page.url
            title = page.title()

            print(f"\n--- Step {step} Captured ---")
            print(f"URL: {current_url}")
            print(f"Title: {title}")

            # Try to find key elements
            forms = page.query_selector_all('form')
            buttons = page.query_selector_all('button, input[type="submit"]')
            links = page.query_selector_all('a')
            tables = page.query_selector_all('table')
            inputs = page.query_selector_all('input:not([type="hidden"])')

            print(f"Forms: {len(forms)}")
            print(f"Buttons: {len(buttons)}")
            print(f"Input fields: {len(inputs)}")
            print(f"Tables: {len(tables)}")
            print(f"Links: {len(links)}")

            # Take screenshot
            screenshot_path = f"screenshots/step_{step}.png"
            try:
                import os
                os.makedirs("screenshots", exist_ok=True)
                page.screenshot(path=screenshot_path)
                print(f"Screenshot: {screenshot_path}")
            except Exception as e:
                print(f"Screenshot failed: {e}")

            print("-" * 30 + "\n")
            step += 1

        browser.close()

    print("Done! Check the screenshots folder for captured screens.")

if __name__ == "__main__":
    main()
