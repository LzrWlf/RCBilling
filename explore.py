#!/usr/bin/env python3
"""
Opens DDS eBilling portal for manual exploration.
Browser stays open for 10 minutes - navigate freely and describe what you see.
"""
from playwright.sync_api import sync_playwright
import time
import os

PORTAL_URL = "https://ebilling.dds.ca.gov:8373/login"

def main():
    print("Opening DDS eBilling portal...")
    print("Browser will stay open for 10 minutes.")
    print("Log in and explore - tell me what you see!\n")

    os.makedirs("screenshots", exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        page.goto(PORTAL_URL, wait_until="networkidle")
        print(f"Portal loaded: {PORTAL_URL}")
        print("\nNavigate freely. I'll take a screenshot every 30 seconds.")
        print("Screenshots saved to: ~/Desktop/RCBilling/screenshots/\n")

        # Take screenshots periodically
        for i in range(20):  # 20 x 30sec = 10 minutes
            time.sleep(30)
            try:
                screenshot_path = f"screenshots/capture_{i+1}.png"
                page.screenshot(path=screenshot_path)
                print(f"[{(i+1)*30}s] Screenshot saved: {screenshot_path} | URL: {page.url}")
            except:
                pass

        browser.close()

if __name__ == "__main__":
    main()
