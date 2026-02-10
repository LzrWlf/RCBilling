"""
DDS eBilling Portal Automation using Playwright

This module handles automated login and invoice submission to the
California DDS (Department of Developmental Services) eBilling portal.

Portal Flow:
1. Login page → Click LAUNCH APPLICATION → Opens popup
2. Enter credentials → Login
3. Accept user agreement
4. Select provider
5. Click Invoices tab
6. Search for invoices
7. Click EDIT on invoice row → navigates to /invoices/invoiceview
8. Click Days Attend "0" → navigates to /invoices/unitcalendar
9. Enter units for each service day in calendar
10. Click Update to save
"""
from playwright.sync_api import sync_playwright, Page, Browser
from dataclasses import dataclass
from typing import List, Optional, Dict, Tuple
from collections import defaultdict
import logging
import time
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Read headless mode from environment (default True for production)
PLAYWRIGHT_HEADLESS = os.environ.get('PLAYWRIGHT_HEADLESS', 'true').lower() == 'true'

# Create screenshots directory for debugging
SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'screenshots')
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

# Portal URLs by Regional Center
RC_PORTAL_URLS = {
    'SGPRC': 'https://ebilling.dds.ca.gov:8379/login',
    'ELARC': 'https://ebilling.dds.ca.gov:8373/login',
}


@dataclass
class SubmissionResult:
    """Result of a billing submission"""
    success: bool
    partial: bool = False  # True if some days entered but not all
    consumer_name: str = ""
    uci: str = ""
    days_entered: int = 0
    days_expected: int = 0  # Total days expected from CSV
    unavailable_days: List[int] = None  # Days that were greyed out/disabled
    already_entered_days: List[int] = None  # Days that already had values (skipped)
    error_message: Optional[str] = None
    # Billing data from RC portal (captured after update)
    rc_units_billed: float = 0.0
    rc_gross_amount: float = 0.0
    rc_net_amount: float = 0.0
    rc_unit_rate: float = 0.0  # Calculated: gross / units
    # Billing data from CSV invoice
    invoice_units: float = 0.0
    invoice_amount: float = 0.0


class DDSeBillingBot:
    """
    Automation bot for DDS eBilling portal.
    """

    def __init__(self, username: str, password: str, headless: bool = None,
                 regional_center: str = 'ELARC', portal_url: str = None):
        self.username = username
        self.password = password
        # Use provided value, or fall back to environment setting
        self.headless = headless if headless is not None else PLAYWRIGHT_HEADLESS
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.context = None
        self.playwright = None
        self.regional_center = regional_center
        # Use provided portal_url, or fall back to hardcoded list
        if portal_url:
            self.portal_url = portal_url
        else:
            self.portal_url = RC_PORTAL_URLS.get(regional_center, RC_PORTAL_URLS['ELARC'])
        logger.info(f"Using portal URL: {self.portal_url} for {regional_center}")

        self.password_expiry_days = None  # Populated if portal shows expiry warning

        # Invoice caching for efficient multi-record processing
        self._invoice_search_cache: List[Dict] = []  # Level 1: Search results
        self._multi_consumer_cache: Dict = {}  # Level 2: Contents inside multi-consumer invoices (keyed by invoice_id or (svc_code, month) tuple)
        self._current_invoice_key: Optional[tuple] = None  # Track currently open invoice (svc_code, svc_month_year)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def start(self):
        """Start browser session"""
        logger.info(f"Starting browser (headless={self.headless})...")
        self.playwright = sync_playwright().start()
        # Use bundled Chromium (no channel) for Docker/production compatibility
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
        )
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.page.set_viewport_size({"width": 1400, "height": 900})
        logger.info("Browser started")

    def stop(self):
        """Close browser session"""
        self.logout()  # End server-side session before closing browser
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        logger.info("Browser closed")

    def logout(self):
        """Log out of the portal to cleanly end the server-side session"""
        try:
            page_text = self.page.evaluate('() => document.body.innerText || ""')
            if 'Logout' in page_text:
                self._js_click("Logout")
                time.sleep(2)
                logger.info("Logged out of portal")
        except Exception as e:
            logger.warning(f"Logout failed: {e}")

    def _js_click(self, text: str) -> bool:
        """Click element containing text using JavaScript - most reliable method"""
        try:
            result = self.page.evaluate(f'''() => {{
                const elements = document.querySelectorAll('input, button, a, span, td');
                for (const el of elements) {{
                    const t = el.value || el.innerText || '';
                    if (t.trim() === '{text}') {{
                        el.click();
                        return true;
                    }}
                }}
                return false;
            }}''')
            return result
        except:
            return False

    def _screenshot(self, name: str):
        """Take a debug screenshot"""
        try:
            path = os.path.join(SCREENSHOT_DIR, f"{name}.png")
            self.page.screenshot(path=path)
            logger.info(f"Screenshot saved: {path}")
        except Exception as e:
            logger.warning(f"Screenshot failed: {e}")

    def _click_launch_button(self) -> bool:
        """Click LAUNCH APPLICATION button using multiple detection strategies"""
        # Strategy 1: JavaScript - find image with onclick or launch link
        clicked = self.page.evaluate('''() => {
            // Try clicking the launch image (common pattern)
            const launchImg = document.querySelector('#launch-box img[onclick], img[onclick*="launch"]');
            if (launchImg) {
                launchImg.click();
                return 'clicked launch image';
            }
            // Try calling launchApp() directly if it exists
            if (typeof launchApp === 'function') {
                launchApp();
                return 'called launchApp()';
            }
            // Try various text-based selectors
            const selectors = [
                'input[value*="LAUNCH" i]',
                'button:contains("LAUNCH")',
                'a:contains("LAUNCH")',
                '[onclick*="launch" i]'
            ];
            for (const sel of selectors) {
                try {
                    const el = document.querySelector(sel);
                    if (el) {
                        el.click();
                        return 'clicked via selector: ' + sel;
                    }
                } catch(e) {}
            }
            // Last resort: find any element with LAUNCH text
            const allElements = document.querySelectorAll('input, button, a, img, span, div');
            for (const el of allElements) {
                const text = (el.value || el.innerText || el.alt || '').toUpperCase();
                if (text.includes('LAUNCH')) {
                    el.click();
                    return 'clicked element with LAUNCH text';
                }
            }
            return false;
        }''')

        if clicked:
            logger.info(f"Launch button: {clicked}")
            return True

        # Strategy 2: Try Playwright text locator
        try:
            self.page.get_by_text("LAUNCH APPLICATION", exact=False).first.click(timeout=3000)
            logger.info("Clicked via Playwright text locator")
            return True
        except:
            pass

        # Strategy 3: Try common button selectors
        launch_selectors = [
            'text="LAUNCH APPLICATION"',
            'a:has-text("LAUNCH APPLICATION")',
            'button:has-text("LAUNCH")',
            'input[value*="LAUNCH" i]',
            '.btn:has-text("Launch")',
            'button.btn-primary',
            'button.btn-lg',
        ]
        for selector in launch_selectors:
            try:
                self.page.click(selector, timeout=2000)
                logger.info(f"Clicked via selector: {selector}")
                return True
            except:
                continue

        logger.error("Could not find LAUNCH APPLICATION button")
        return False

    def login(self) -> bool:
        """Login to the portal"""
        try:
            logger.info(f"Navigating to {self.portal_url}")
            self.page.goto(self.portal_url, wait_until="networkidle")
            time.sleep(2)
            self._screenshot("01_landing_page")

            # Click LAUNCH APPLICATION button - this opens a popup window
            logger.info("Clicking LAUNCH APPLICATION...")

            # Wait for popup when clicking launch
            with self.context.expect_page(timeout=15000) as popup_info:
                self._click_launch_button()

            # Switch to the popup window
            popup = popup_info.value
            popup.wait_for_load_state("domcontentloaded")
            self.page = popup
            logger.info("Switched to login popup window")

            time.sleep(2)
            self._screenshot("02_login_popup")

            # Find username field - try multiple selectors
            logger.info("Entering credentials...")
            username_filled = False
            username_selectors = [
                'input[type="text"]',
                'input[name="username"]',
                'input[name="userName"]',
                'input[name="user"]',
                'input[id="username"]',
                'input[id="userName"]',
            ]

            for selector in username_selectors:
                try:
                    username_input = self.page.query_selector(selector)
                    if username_input:
                        username_input.fill(self.username)
                        username_filled = True
                        logger.info(f"Filled username using selector: {selector}")
                        break
                except Exception as e:
                    continue

            if not username_filled:
                # Try JavaScript approach - find input near "Username" text
                username_filled = self.page.evaluate(f'''() => {{
                    const inputs = document.querySelectorAll('input');
                    for (const input of inputs) {{
                        const type = input.type.toLowerCase();
                        if (type === 'text' || type === '') {{
                            input.value = '{self.username}';
                            input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            return true;
                        }}
                    }}
                    return false;
                }}''')
                if username_filled:
                    logger.info("Filled username using JavaScript")

            if not username_filled:
                logger.error("Could not find username field")
                self._screenshot("error_no_username_field")
                return False

            # Find and fill password
            password_filled = False
            password_input = self.page.query_selector('input[type="password"]')
            if password_input:
                password_input.fill(self.password)
                password_filled = True
                logger.info("Filled password field")

            if not password_filled:
                logger.error("Could not find password field")
                self._screenshot("error_no_password_field")
                return False

            self._screenshot("03_credentials_entered")

            # Click Login button
            login_clicked = False
            login_selectors = [
                'input[type="submit"][value="Login"]',
                'input[value="Login"]',
                'button:has-text("Login")',
                'input[type="submit"]',
            ]
            for selector in login_selectors:
                try:
                    self.page.click(selector, timeout=3000)
                    login_clicked = True
                    logger.info(f"Clicked login using selector: {selector}")
                    break
                except:
                    continue

            if not login_clicked:
                # Try pressing Enter on password field
                password_input.press("Enter")
                logger.info("Pressed Enter to submit")

            self.page.wait_for_load_state("networkidle")
            time.sleep(3)
            self._screenshot("04_after_login")

            # Check for login errors
            error_elem = self.page.query_selector('.error, .alert-danger, .login-error, [class*="error"]')
            if error_elem:
                error_text = error_elem.inner_text()
                if 'invalid' in error_text.lower() or 'incorrect' in error_text.lower():
                    logger.error(f"Login failed: {error_text}")
                    self._screenshot("error_login_failed")
                    return False

            # Check if we're still on login page (login failed)
            current_url = self.page.url
            if '/login' in current_url:
                # Check page content for any error indicators
                page_text = self.page.content().lower()
                if 'invalid' in page_text or 'incorrect' in page_text or 'failed' in page_text:
                    logger.error("Login appears to have failed - still on login page")
                    self._screenshot("error_still_on_login")
                    return False

            # Post-login: handle dialogs in whatever order the portal presents them
            import re
            for attempt in range(5):  # Max 5 rounds of dialog handling
                time.sleep(2)
                try:
                    page_text = self.page.evaluate('() => document.body.innerText || ""')
                except:
                    break

                self._screenshot(f"05_post_login_round_{attempt}")
                logger.info(f"Post-login round {attempt}: checking page state...")

                # 1. Check for password expiry overlay
                expiry_match = re.search(r'password will expire in (\d+) day', page_text)
                if expiry_match:
                    self.password_expiry_days = int(expiry_match.group(1))
                    logger.warning(f"Password expires in {self.password_expiry_days} days")
                    self._js_click("OK")
                    time.sleep(1)
                    continue  # Re-check page after dismissing

                # 2. Check for agreement dialog (must come before User Profile check
                #    because "My Profile" nav link appears on every page including Dashboard)
                if 'I do not agree' in page_text:
                    logger.info("Accepting user agreement...")
                    self.page.evaluate('''() => {
                        const elements = document.querySelectorAll('input, button, a');
                        for (const el of elements) {
                            const text = (el.value || el.innerText || '').trim();
                            if (text === 'Accept' || text === 'I Agree' || text === 'ACCEPT') {
                                el.click();
                                return true;
                            }
                        }
                        return false;
                    }''')
                    try:
                        self.page.wait_for_load_state("networkidle")
                    except:
                        pass
                    time.sleep(1)
                    continue  # Re-check page after accepting

                # 3. Check for User Profile / change password form
                #    Use "User Profile of" to match the page heading, not the "My Profile" nav link
                if 'User Profile of' in page_text:
                    logger.info("On User Profile page, clicking Close...")
                    self._js_click("Close")
                    time.sleep(1)
                    try:
                        self.page.wait_for_load_state("networkidle")
                    except:
                        pass
                    time.sleep(1)
                    continue  # Re-check page after closing

                # 4. If we see Service Provider Selection, we're done
                if 'Service Provider Selection' in page_text:
                    logger.info("Reached Service Provider Selection — login complete")
                    break

            self._screenshot("05_after_navigation")
            logger.info("Login successful")
            return True

        except Exception as e:
            logger.error(f"Login failed: {e}")
            self._screenshot("error_login_exception")
            return False

    def select_first_provider(self) -> bool:
        """Select the first available provider in the list"""
        try:
            logger.info("Selecting first available provider...")
            self._screenshot("05_provider_selection")

            # Dojo DataGrid multi-view: each column in separate view, so each row has 1 cell.
            # Search individual cells for SPN pattern.
            result = self.page.evaluate('''() => {
                const allCells = document.querySelectorAll('.dojoxGridCell');
                for (const cell of allCells) {
                    const text = (cell.innerText || '').trim();
                    if (/^[A-Za-z]{2}\\d+$/.test(text)) {
                        const row = cell.closest('.dojoxGridRow');
                        if (row) { row.click(); return 'clicked provider: ' + text; }
                        cell.click();
                        return 'clicked provider cell: ' + text;
                    }
                }
                // Fallback: search plain td elements
                const tds = document.querySelectorAll('td');
                for (const td of tds) {
                    const text = (td.innerText || '').trim();
                    if (/^[A-Za-z]{2}\\d+$/.test(text)) {
                        const row = td.closest('tr');
                        if (row) { row.click(); return 'clicked provider row: ' + text; }
                    }
                }
                return false;
            }''')

            if result:
                logger.info(f"Provider selection: {result}")
                time.sleep(2)
                self._js_click("OK")
                self.page.wait_for_load_state("networkidle")
                time.sleep(2)
                self._screenshot("06_after_provider_select")
                logger.info("First provider selected")
                return True
            logger.warning("No provider rows found")
            return False
        except Exception as e:
            logger.error(f"Failed to select first provider: {e}")
            return False

    def get_available_providers(self) -> List[Dict]:
        """Read all providers from the provider selection table without clicking any."""
        try:
            logger.info("Reading available providers from table...")
            time.sleep(1)
            self._screenshot("provider_table_read")

            # Diagnostic: log grid structure to confirm multi-view layout
            diag = self.page.evaluate('''() => {
                return {
                    dojoxGridRows: document.querySelectorAll('.dojoxGridRow').length,
                    dojoxGridCells: document.querySelectorAll('.dojoxGridCell').length,
                    sampleCells: Array.from(document.querySelectorAll('.dojoxGridCell'))
                        .slice(0, 10).map(c => (c.innerText || '').trim().substring(0, 20))
                };
            }''')
            logger.info(f"Grid diagnostic: {diag}")

            # Dojo DataGrid multi-view layout: each column is in a separate "view",
            # so each .dojoxGridRow contains only 1 cell. Search ALL cells individually.
            providers = self.page.evaluate('''() => {
                const results = [];
                const seen = new Set();
                const allCells = document.querySelectorAll('.dojoxGridCell');
                for (const cell of allCells) {
                    const text = (cell.innerText || '').trim();
                    if (/^[A-Za-z]{2}\\d+$/.test(text) && !seen.has(text.toUpperCase())) {
                        seen.add(text.toUpperCase());
                        let name = '';
                        const row = cell.closest('.dojoxGridRow');
                        const content = row?.closest('.dojoxGridContent, .dojoxGridScrollbox');
                        if (content) {
                            const rowIdx = Array.from(
                                content.querySelectorAll('.dojoxGridRow')
                            ).indexOf(row);
                            const view = content.closest('.dojoxGridView');
                            const allViews = view?.parentElement?.querySelectorAll(':scope > .dojoxGridView') || [];
                            for (const v of allViews) {
                                if (v === view) continue;
                                const otherRows = v.querySelectorAll('.dojoxGridContent .dojoxGridRow, .dojoxGridScrollbox .dojoxGridRow');
                                if (otherRows[rowIdx]) {
                                    const otherText = (otherRows[rowIdx].innerText || '').trim();
                                    if (otherText && !/^[A-Za-z]{2}\\d+$/.test(otherText)) {
                                        name = otherText;
                                    }
                                }
                            }
                        }
                        results.push({ spn_id: text.toUpperCase(), name: name });
                    }
                }
                // Fallback: try plain td cells if dojoxGridCell returned nothing
                if (results.length === 0) {
                    const tds = document.querySelectorAll('td');
                    for (const td of tds) {
                        const text = (td.innerText || '').trim();
                        if (/^[A-Za-z]{2}\\d+$/.test(text) && !seen.has(text.toUpperCase())) {
                            seen.add(text.toUpperCase());
                            // Get description from next sibling td
                            const nextTd = td.nextElementSibling;
                            const name = nextTd ? (nextTd.innerText || '').trim() : '';
                            results.push({ spn_id: text.toUpperCase(), name: name });
                        }
                    }
                }
                return results;
            }''')

            logger.info(f"Found {len(providers)} providers in table")
            for p in providers:
                logger.info(f"  Provider: {p['spn_id']} - {p['name']}")
            return providers
        except Exception as e:
            logger.error(f"Failed to read provider table: {e}")
            return []

    def select_provider(self, provider_identifier: str) -> bool:
        """Select the service provider by SPN ID or name"""
        try:
            logger.info(f"Selecting provider: {provider_identifier}")
            self._screenshot("05_provider_selection")

            clicked = False

            # Method 1: Try exact SPN ID match — search individual cells (multi-view layout)
            clicked = self.page.evaluate(f'''() => {{
                const allCells = document.querySelectorAll('.dojoxGridCell');
                for (const cell of allCells) {{
                    const text = (cell.innerText || '').trim();
                    if (text.toUpperCase() === '{provider_identifier}'.toUpperCase()) {{
                        const row = cell.closest('.dojoxGridRow');
                        if (row) {{ row.click(); return true; }}
                        cell.click();
                        return true;
                    }}
                }}
                return false;
            }}''')

            if clicked:
                logger.info(f"Selected provider by exact SPN ID: {provider_identifier}")

            # Method 2: Try matching just the numeric portion of SPN ID
            # (handles cases where prefix differs, e.g., HP1829 vs PP1829)
            if not clicked:
                numeric_part = ''.join(c for c in provider_identifier if c.isdigit())
                if numeric_part:
                    clicked = self.page.evaluate(f'''() => {{
                        const allCells = document.querySelectorAll('.dojoxGridCell');
                        for (const cell of allCells) {{
                            const text = (cell.innerText || '').trim();
                            if (text.match(/[A-Za-z]+{numeric_part}$/i)) {{
                                const row = cell.closest('.dojoxGridRow');
                                if (row) {{ row.click(); return true; }}
                                cell.click();
                                return true;
                            }}
                        }}
                        return false;
                    }}''')
                    if clicked:
                        logger.info(f"Selected provider by numeric match: {numeric_part}")

            # Method 3: Fall back to provider name text match (case-insensitive)
            if not clicked:
                # Escape special characters for JavaScript string
                safe_identifier = provider_identifier.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
                clicked = self.page.evaluate(f'''() => {{
                    const searchTerm = '{safe_identifier}'.toLowerCase();
                    const allCells = document.querySelectorAll('.dojoxGridCell');
                    for (const cell of allCells) {{
                        if ((cell.innerText || '').toLowerCase().includes(searchTerm)) {{
                            const row = cell.closest('.dojoxGridRow');
                            if (row) {{ row.click(); return true; }}
                            cell.click();
                            return true;
                        }}
                    }}
                    return false;
                }}''')
                if clicked:
                    logger.info(f"Selected provider by name match: {provider_identifier}")

            # Method 4: Fallback to plain td elements
            if not clicked:
                safe_identifier = provider_identifier.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
                clicked = self.page.evaluate(f'''() => {{
                    const tds = document.querySelectorAll('td');
                    for (const td of tds) {{
                        const text = (td.innerText || '').trim();
                        if (text.toUpperCase() === '{safe_identifier}'.toUpperCase() ||
                            text.toLowerCase().includes('{safe_identifier}'.toLowerCase())) {{
                            const row = td.closest('tr');
                            if (row) {{ row.click(); return true; }}
                        }}
                    }}
                    // Also try SPN pattern match on td elements
                    for (const td of tds) {{
                        const text = (td.innerText || '').trim();
                        if (/^[A-Za-z]{{2}}\\d+$/.test(text)) {{
                            const numericPart = text.replace(/[A-Za-z]/g, '');
                            const searchNumeric = '{safe_identifier}'.replace(/[A-Za-z]/g, '');
                            if (numericPart === searchNumeric) {{
                                const row = td.closest('tr');
                                if (row) {{ row.click(); return true; }}
                            }}
                        }}
                    }}
                    return false;
                }}''')
                if clicked:
                    logger.info(f"Selected provider by td fallback: {provider_identifier}")

            if not clicked:
                logger.error(f"Provider '{provider_identifier}' not found")
                self._screenshot("error_provider_not_found")
                return False

            time.sleep(2)

            # Click OK on confirmation dialog if present
            try:
                self._js_click("OK")
                time.sleep(1)
            except:
                pass

            self.page.wait_for_load_state("networkidle")
            time.sleep(2)
            self._screenshot("06_after_provider_select")
            logger.info(f"Provider '{provider_identifier}' selected")
            return True

        except Exception as e:
            logger.error(f"Provider selection failed: {e}")
            self._screenshot("error_provider_selection")
            return False

    def navigate_to_invoices(self) -> bool:
        """Navigate to Invoices tab and search"""
        try:
            logger.info("Clicking Invoices tab...")

            # Try multiple methods to click Invoices tab
            clicked = False
            tab_selectors = [
                'a:has-text("Invoices")',
                'li:has-text("Invoices") a',
                'nav a:has-text("Invoices")',
                '*[role="tab"]:has-text("Invoices")',
            ]
            for selector in tab_selectors:
                try:
                    self.page.click(selector, timeout=3000)
                    clicked = True
                    break
                except:
                    continue

            if not clicked:
                # Try JavaScript
                clicked = self._js_click("Invoices")

            if not clicked:
                logger.error("Could not find Invoices tab")
                self._screenshot("error_no_invoices_tab")
                return False

            self.page.wait_for_load_state("networkidle")
            time.sleep(2)
            self._screenshot("07_invoices_tab")

            # Click Search button - try multiple methods
            logger.info("Clicking Search button...")
            search_clicked = False

            # Method 1: Direct button click
            search_selectors = [
                'input[value="Search"]',
                'button:has-text("Search")',
                'input[type="button"][value="Search"]',
                'input[type="submit"][value="Search"]',
            ]
            for selector in search_selectors:
                try:
                    self.page.click(selector, timeout=3000)
                    search_clicked = True
                    logger.info(f"Clicked Search via: {selector}")
                    break
                except:
                    continue

            # Method 2: JavaScript click
            if not search_clicked:
                search_clicked = self.page.evaluate('''() => {
                    const buttons = document.querySelectorAll('input, button');
                    for (const btn of buttons) {
                        const val = (btn.value || btn.innerText || '').trim();
                        if (val === 'Search') {
                            btn.click();
                            return true;
                        }
                    }
                    return false;
                }''')
                if search_clicked:
                    logger.info("Clicked Search via JavaScript")

            if not search_clicked:
                logger.warning("Could not click Search button")

            self.page.wait_for_load_state("networkidle")
            time.sleep(2)

            # Wait for table content to appear (invoice IDs are 7-digit numbers)
            logger.info("Waiting for invoice table to load...")
            try:
                # Wait up to 10 seconds for invoice data to appear
                self.page.wait_for_function(
                    '''() => {
                        // Check standard td cells
                        const tds = document.querySelectorAll('td');
                        for (const td of tds) {
                            if (/^\\d{7}$/.test((td.innerText || '').trim())) return true;
                        }
                        // Check Dojo DataGrid cells
                        const gridCells = document.querySelectorAll('.dojoxGridCell');
                        for (const cell of gridCells) {
                            if (/^\\d{7}$/.test((cell.innerText || '').trim())) return true;
                        }
                        return false;
                    }''',
                    timeout=10000
                )
                logger.info("Invoice table data loaded successfully")
            except Exception as e:
                logger.warning(f"Timeout waiting for invoice table data: {e}")
                # Continue anyway - the table might be empty legitimately

            time.sleep(1)
            self._screenshot("08_after_search")

            return True
        except Exception as e:
            logger.error(f"Navigation failed: {e}")
            self._screenshot("error_navigation")
            return False

    def navigate_to_provider_selection(self) -> bool:
        """Navigate back to Service Provider Selection (Dashboard)"""
        try:
            logger.info("Navigating back to Service Provider Selection...")
            self._screenshot("nav_back_before")

            # Strategy 1: Click "Home" tab (main nav) — this is the primary nav tab
            self._js_click("Home")
            time.sleep(2)
            self.page.wait_for_load_state("networkidle")
            time.sleep(1)

            page_text = self.page.evaluate('() => document.body.innerText || ""')
            if 'Service Provider Selection' in page_text:
                logger.info("Back at Service Provider Selection via Home tab")
                self._screenshot("nav_back_success")
                return True

            # Strategy 2: Click "Dashboard" sub-tab (under Home)
            self._js_click("Dashboard")
            time.sleep(2)
            self.page.wait_for_load_state("networkidle")
            time.sleep(1)

            page_text = self.page.evaluate('() => document.body.innerText || ""')
            if 'Service Provider Selection' in page_text:
                logger.info("Back at Service Provider Selection via Dashboard sub-tab")
                self._screenshot("nav_back_success")
                return True

            # Strategy 3: Try Playwright selectors for nav links
            for link_text in ["Home", "Dashboard", "Service Provider"]:
                try:
                    self.page.click(f'a:has-text("{link_text}")', timeout=3000)
                    time.sleep(2)
                    self.page.wait_for_load_state("networkidle")
                    page_text = self.page.evaluate('() => document.body.innerText || ""')
                    if 'Service Provider Selection' in page_text:
                        logger.info(f"Back at Service Provider Selection via '{link_text}' link")
                        self._screenshot("nav_back_success")
                        return True
                except:
                    continue

            logger.warning("Could not navigate back to Service Provider Selection")
            self._screenshot("nav_back_failed")
            return False
        except Exception as e:
            logger.warning(f"navigate_to_provider_selection failed: {e}")
            self._screenshot("nav_back_error")
            return False

    def _debug_page_structure(self):
        """Debug helper to understand page structure"""
        info = self.page.evaluate('''() => {
            const tables = document.querySelectorAll('table');
            const iframes = document.querySelectorAll('iframe');
            const allTrs = document.querySelectorAll('tr');

            let tableInfo = [];
            tables.forEach((t, i) => {
                const rows = t.querySelectorAll('tr').length;
                const text = t.innerText.substring(0, 100);
                tableInfo.push({index: i, rows: rows, preview: text});
            });

            return {
                tableCount: tables.length,
                iframeCount: iframes.length,
                totalTrCount: allTrs.length,
                tables: tableInfo,
                bodyText: document.body.innerText.substring(0, 500)
            };
        }''')
        logger.info(f"Page structure: {info}")
        return info

    def _wait_for_invoice_table(self, timeout: int = 10) -> bool:
        """
        Wait for invoice table to have data rows.
        Detects invoice table by looking for 7-digit invoice IDs in either
        standard HTML table cells or Dojo DataGrid cells.
        Returns True if table data found, False if timeout.
        """
        import time as time_module
        start = time_module.time()
        while time_module.time() - start < timeout:
            has_data = self.page.evaluate('''() => {
                // Check standard HTML table cells
                const tds = document.querySelectorAll('td');
                for (const td of tds) {
                    if (/^\\d{7}$/.test((td.innerText || '').trim())) return true;
                }
                // Check Dojo DataGrid cells
                const gridCells = document.querySelectorAll('.dojoxGridCell');
                for (const cell of gridCells) {
                    if (/^\\d{7}$/.test((cell.innerText || '').trim())) return true;
                }
                return false;
            }''')
            if has_data:
                logger.info("Invoice table data detected")
                return True
            time_module.sleep(0.5)
        logger.warning("Timeout waiting for invoice table data")
        return False

    def cache_invoice_search_results(self) -> list:
        """
        Scrape invoice search results table and return list of invoice info.
        Uses robust table detection by finding tables with 7-digit invoice IDs.
        Column structure (0-indexed):
          - Column 0: Checkbox
          - Column 1: Invoice #
          - Column 2: Service Code
          - Column 3: Service M/Y
          - Column 4: UCI#
          - Column 5: Consumer Name
          - Column 6+: Other fields (Invoice Date, etc.)
        """
        try:
            # First, wait for table data to be present
            self._wait_for_invoice_table(timeout=10)

            # Find and extract invoices using data-pattern detection (not header text)
            result = self.page.evaluate('''() => {
                const invoices = [];
                const tables = document.querySelectorAll('table');
                const debug = {
                    tableCount: tables.length,
                    tableSummary: [],
                    bestTableIndex: -1,
                    bestTableInvoiceCount: 0
                };

                // Find the table with the most invoice-like rows (7-digit IDs in column 1)
                let bestTable = null;
                let bestCount = 0;

                for (let i = 0; i < tables.length; i++) {
                    const table = tables[i];
                    const rows = table.querySelectorAll('tr');
                    let invoiceRowCount = 0;

                    for (const row of rows) {
                        const cells = row.querySelectorAll('td');
                        if (cells.length >= 6) {
                            // Check if cell 1 looks like an invoice ID (7 digits)
                            const cell1Text = cells[1]?.innerText?.trim() || '';
                            if (/^\\d{7}$/.test(cell1Text)) {
                                invoiceRowCount++;
                            }
                        }
                    }

                    debug.tableSummary.push({
                        index: i,
                        rowCount: rows.length,
                        invoiceRowCount: invoiceRowCount
                    });

                    if (invoiceRowCount > bestCount) {
                        bestCount = invoiceRowCount;
                        bestTable = table;
                        debug.bestTableIndex = i;
                        debug.bestTableInvoiceCount = invoiceRowCount;
                    }
                }

                if (!bestTable) {
                    return { invoices: invoices, debug: debug };
                }

                // Now extract data from the best table
                const rows = bestTable.querySelectorAll('tr');
                let rowIndex = 0;

                for (const row of rows) {
                    const cells = row.querySelectorAll('td');
                    if (cells.length < 6) continue;

                    const invoiceId = cells[1]?.innerText?.trim() || '';
                    if (!/^\\d{7}$/.test(invoiceId)) continue;

                    const svcCode = cells[2]?.innerText?.trim() || '';
                    const svcMonth = cells[3]?.innerText?.trim() || '';
                    const uci = cells[4]?.innerText?.trim() || '';
                    const consumerName = cells[5]?.innerText?.trim() || '';

                    // Basic validation
                    if (!/^\\d+$/.test(svcCode)) continue;
                    if (!svcMonth.includes('/')) continue;

                    invoices.push({
                        invoice_id: invoiceId,
                        svc_code: svcCode,
                        svc_month: svcMonth,
                        uci: uci,
                        consumer_name: consumerName,
                        row_index: rowIndex,
                        has_uci: uci.length > 0
                    });
                    rowIndex++;
                }

                return { invoices: invoices, debug: debug };
            }''')

            # Extract debug info and invoices
            debug_info = result.get('debug', {})
            invoices = result.get('invoices', [])

            # Log debug information
            logger.info(f"Table detection: {debug_info.get('tableCount', 0)} tables found")
            if debug_info.get('tableSummary'):
                for ts in debug_info['tableSummary']:
                    logger.info(f"  Table {ts['index']}: {ts['rowCount']} rows, {ts['invoiceRowCount']} invoice rows")
            if debug_info.get('bestTableIndex', -1) >= 0:
                logger.info(f"Selected table {debug_info['bestTableIndex']} with {debug_info['bestTableInvoiceCount']} invoice rows")
            else:
                logger.warning("No invoice table found in standard HTML tables, trying Dojo DataGrid...")

            # Fallback: Dojo DataGrid (multi-view layout where each column is a separate view)
            if not invoices:
                invoices = self._scrape_invoices_from_dojo_grid()

            logger.info(f"Found {len(invoices)} invoices in table")
            for inv in invoices:
                logger.info(f"  Invoice {inv['invoice_id']}: SVC={inv['svc_code']}, Month={inv['svc_month']}, UCI={inv['uci'] or '(multi)'}")

            return invoices

        except Exception as e:
            logger.error(f"Failed to cache invoice search results: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []

    def _scrape_visible_dojo_rows(self) -> list:
        """Scrape currently visible invoice rows from Dojo DataGrid.
        Each column lives in a separate 'view' div. We merge cells across
        views by row index to reconstruct full rows."""
        result = self.page.evaluate('''() => {
            const views = document.querySelectorAll('.dojoxGridView');
            if (!views.length) return { invoices: [], debug: 'no dojoxGridView found' };

            // Collect cell texts from each view, organized by row
            const viewData = [];
            for (const view of views) {
                const rows = view.querySelectorAll(
                    '.dojoxGridContent .dojoxGridRow, .dojoxGridScrollbox .dojoxGridRow'
                );
                const rowTexts = [];
                for (const row of rows) {
                    const cells = row.querySelectorAll('.dojoxGridCell');
                    const texts = Array.from(cells).map(c => (c.innerText || '').trim());
                    rowTexts.push(texts);
                }
                viewData.push(rowTexts);
            }

            // Merge views into virtual rows (concatenate cells from each view)
            const rowCount = Math.max(...viewData.map(v => v.length), 0);
            const virtualRows = [];
            for (let r = 0; r < rowCount; r++) {
                const row = [];
                for (const vd of viewData) {
                    if (vd[r]) row.push(...vd[r]);
                }
                virtualRows.push(row);
            }

            // Find invoice rows: look for a 7-digit number in each virtual row
            const invoices = [];
            for (let r = 0; r < virtualRows.length; r++) {
                const cells = virtualRows[r];
                // Find the invoice ID cell (7 digits)
                let idIdx = -1;
                for (let c = 0; c < cells.length; c++) {
                    if (/^\\d{7}$/.test(cells[c])) { idIdx = c; break; }
                }
                if (idIdx < 0) continue;

                const invoiceId = cells[idIdx];
                const svcCode = cells[idIdx + 1] || '';
                const svcMonth = cells[idIdx + 2] || '';
                const uci = cells[idIdx + 3] || '';
                const consumerName = cells[idIdx + 4] || '';

                // Validate: svcCode should be numeric, svcMonth should contain /
                if (!/^\\d+$/.test(svcCode)) continue;
                if (!svcMonth.includes('/')) continue;

                invoices.push({
                    invoice_id: invoiceId,
                    svc_code: svcCode,
                    svc_month: svcMonth,
                    uci: uci,
                    consumer_name: consumerName,
                    row_index: r,
                    has_uci: uci.length > 0
                });
            }

            return {
                invoices: invoices,
                debug: {
                    viewCount: views.length,
                    rowCount: rowCount,
                    sampleRow: virtualRows.length > 0 ? virtualRows[0].join(' | ') : '(empty)'
                }
            };
        }''')
        return result

    def _scrape_invoices_from_dojo_grid(self) -> list:
        """Scrape ALL invoice data from Dojo DataGrid by scrolling through
        the virtual scroll container. The grid only renders ~30 rows at a
        time, so we scroll down incrementally to render and capture all rows,
        deduplicating by invoice_id."""
        try:
            all_invoices = {}  # keyed by invoice_id to deduplicate
            max_scroll_iterations = 200  # safety limit

            # Initial scrape of visible rows
            result = self._scrape_visible_dojo_rows()
            debug = result.get('debug', {})
            logger.info(f"Dojo DataGrid initial scrape: {debug}")

            for inv in result.get('invoices', []):
                all_invoices[inv['invoice_id']] = inv

            logger.info(f"Initial visible rows: {len(result.get('invoices', []))} invoices "
                        f"({len(all_invoices)} unique)")

            # Scroll loop: scroll the .dojoxGridScrollbox container down
            for scroll_iter in range(max_scroll_iterations):
                # Scroll down by one viewport height within the grid scrollbox
                scroll_result = self.page.evaluate('''() => {
                    // Find the scrollbox container(s) - pick the one with scrollable content
                    const scrollboxes = document.querySelectorAll('.dojoxGridScrollbox');
                    for (const sb of scrollboxes) {
                        if (sb.scrollHeight > sb.clientHeight) {
                            const prevTop = sb.scrollTop;
                            sb.scrollTop += sb.clientHeight - 20;
                            return {
                                scrolled: sb.scrollTop !== prevTop,
                                scrollTop: sb.scrollTop,
                                clientHeight: sb.clientHeight,
                                scrollHeight: sb.scrollHeight,
                                atBottom: (sb.scrollTop + sb.clientHeight) >= (sb.scrollHeight - 5)
                            };
                        }
                    }
                    return { scrolled: false, atBottom: true, noScrollbox: true };
                }''')

                if not scroll_result.get('scrolled', False):
                    logger.info(f"Scroll loop done: no more scrolling possible "
                                f"(iter {scroll_iter + 1})")
                    break

                # Wait for virtual rows to re-render after scroll
                time.sleep(1.0)

                # Scrape newly visible rows
                result = self._scrape_visible_dojo_rows()
                new_count = 0
                for inv in result.get('invoices', []):
                    if inv['invoice_id'] not in all_invoices:
                        all_invoices[inv['invoice_id']] = inv
                        new_count += 1

                if scroll_iter % 10 == 0 or new_count > 0:
                    logger.info(f"Scroll iter {scroll_iter + 1}: "
                                f"+{new_count} new invoices, "
                                f"{len(all_invoices)} total unique, "
                                f"scrollTop={scroll_result.get('scrollTop', '?')}/"
                                f"{scroll_result.get('scrollHeight', '?')}")

                # Stop if we've reached the bottom
                if scroll_result.get('atBottom', False):
                    logger.info(f"Reached bottom of grid after {scroll_iter + 1} scroll(s)")
                    break

            invoices = list(all_invoices.values())
            logger.info(f"Dojo DataGrid total: {len(invoices)} unique invoices "
                        f"(after scroll + dedup)")
            return invoices

        except Exception as e:
            logger.error(f"Dojo DataGrid invoice scrape failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []

    def _click_next_page(self) -> bool:
        """Click next page button if available, return True if successful"""
        try:
            # Try common pagination patterns
            result = self.page.evaluate('''() => {
                // Look for Next button or > arrow
                const nextSelectors = [
                    'a:contains("Next")', 'input[value="Next"]', 'button:contains("Next")',
                    'a:contains(">")', 'button:contains(">")',
                    '[class*="next"]', '[aria-label*="next" i]',
                    'a.next', 'li.next a'
                ];

                // Try each selector
                for (const sel of nextSelectors) {
                    try {
                        const el = document.querySelector(sel);
                        if (el && !el.disabled && el.offsetParent !== null) {
                            el.click();
                            return true;
                        }
                    } catch(e) {}
                }

                // Try finding by text content
                const links = document.querySelectorAll('a, button, input[type="button"]');
                for (const link of links) {
                    const text = (link.value || link.innerText || '').trim();
                    if (text === 'Next' || text === '>' || text === '>>') {
                        if (!link.disabled && link.offsetParent !== null) {
                            link.click();
                            return true;
                        }
                    }
                }

                return false;
            }''')

            if result:
                time.sleep(2)
                self.page.wait_for_load_state("networkidle")
                return True
            return False

        except Exception as e:
            logger.debug(f"No next page: {e}")
            return False

    def scrape_all_invoice_pages(self) -> List[Dict]:
        """
        Scrape ALL invoice search results, handling pagination.
        Returns complete list of available invoices on the portal.
        """
        all_invoices = []
        page_num = 1
        max_pages = 50  # Safety limit

        while page_num <= max_pages:
            # Debug: Log page structure before scraping (first page only)
            if page_num == 1:
                self._debug_page_structure()
                self._screenshot("invoice_search_before_scrape")

            # Scrape current page with retry on first page
            page_invoices = self.cache_invoice_search_results()

            # On first page, if no invoices found, wait and retry
            if not page_invoices and page_num == 1:
                logger.warning("No invoices found on first attempt, waiting and retrying...")
                time.sleep(3)
                self._screenshot("invoice_search_retry")
                page_invoices = self.cache_invoice_search_results()

                if not page_invoices:
                    logger.error("Still no invoices found after retry. Taking debug screenshot.")
                    self._screenshot("no_invoices_found_debug")
                    # Log the full page HTML for debugging
                    try:
                        html_preview = self.page.evaluate('() => document.body.innerHTML.substring(0, 2000)')
                        logger.error(f"Page HTML preview: {html_preview}")
                    except:
                        pass
                    break

            if not page_invoices:
                break

            # Check for duplicates (indicates we've looped back)
            if all_invoices and page_invoices:
                first_new_id = page_invoices[0].get('invoice_id', '')
                if any(inv.get('invoice_id') == first_new_id for inv in all_invoices):
                    logger.info("Detected duplicate invoices, stopping pagination")
                    break

            all_invoices.extend(page_invoices)
            logger.info(f"Page {page_num}: Found {len(page_invoices)} invoices (total: {len(all_invoices)})")

            # Try to go to next page
            if not self._click_next_page():
                logger.info("No more pages available")
                break

            page_num += 1
            time.sleep(1)

        logger.info(f"=== Invoice Inventory Complete: {len(all_invoices)} invoices across {page_num} page(s) ===")
        return all_invoices

    def _normalize_month(self, month_str: str) -> str:
        """Normalize month format: '8/2025' -> '08/2025'"""
        if not month_str:
            return ''
        parts = month_str.split('/')
        if len(parts) == 2:
            return parts[0].zfill(2) + '/' + parts[1]
        return month_str

    def match_records_to_inventory(
        self,
        records: List[Dict],
        inventory: List[Dict]
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        Match CSV records against portal inventory.

        Returns:
            (matchable_records, unmatched_records)
            Each unmatched record includes 'skip_reason' field
        """
        matchable = []
        unmatched = []

        # Create lookup structures for efficient matching
        # Key: (svc_code, normalized_month) -> list of inventory items
        inventory_by_key = defaultdict(list)
        for inv in inventory:
            key = (inv.get('svc_code', ''), self._normalize_month(inv.get('svc_month', '')))
            inventory_by_key[key].append(inv)

        # Also create UCI lookup for direct matches
        inventory_by_uci = {}
        for inv in inventory:
            uci = inv.get('uci', '')
            if uci:
                month = self._normalize_month(inv.get('svc_month', ''))
                inventory_by_uci[(uci, month)] = inv

        for record in records:
            svc_code = record.get('svc_code', '')
            service_month = self._normalize_month(record.get('service_month', ''))
            uci = record.get('uci', '')
            consumer_name = record.get('consumer_name', '')

            # Method 1: Direct UCI + month match
            if (uci, service_month) in inventory_by_uci:
                matchable.append(record)
                logger.debug(f"  ✓ Direct match: {consumer_name} (UCI: {uci})")
                continue

            # Method 2: Check by service code + month (for multi-consumer invoices)
            key = (svc_code, service_month)
            matching_invoices = inventory_by_key.get(key, [])

            if not matching_invoices:
                # No invoice for this service code + month
                record['skip_reason'] = f"No invoice found for SVC {svc_code}, Month {service_month}"
                unmatched.append(record)
                logger.debug(f"  ✗ No invoice: {consumer_name} - {record['skip_reason']}")
                continue

            # Check for multi-consumer invoice (empty UCI means it could contain this consumer)
            found_multi_consumer = False
            for inv in matching_invoices:
                if not inv.get('has_uci', True):  # Empty UCI = multi-consumer invoice
                    found_multi_consumer = True
                    record['_is_multi_consumer'] = True
                    break

            if found_multi_consumer:
                matchable.append(record)
                logger.debug(f"  ✓ Multi-consumer match: {consumer_name} (UCI: {uci})")
            else:
                # Invoice exists but UCI not in it
                record['skip_reason'] = f"UCI {uci} not found in available invoices for SVC {svc_code}, Month {service_month}"
                unmatched.append(record)
                logger.debug(f"  ✗ UCI not found: {consumer_name} - {record['skip_reason']}")

        logger.info(f"Matching complete: {len(matchable)} matchable, {len(unmatched)} unmatched")
        return matchable, unmatched

    def expand_multi_consumer_folder(self, folder_inv: Dict) -> List[Dict]:
        """
        Click into a multi-consumer invoice folder and scrape individual invoices inside.
        Returns list of individual consumer invoice records.
        """
        try:
            svc_code = folder_inv.get('svc_code', '')
            svc_month = folder_inv.get('svc_month', '')
            invoice_id = folder_inv.get('invoice_id', '')

            logger.info(f"Expanding folder: Invoice {invoice_id}, SVC={svc_code}, Month={svc_month}")

            # Check cache first to avoid unnecessary click
            if invoice_id and invoice_id in self._multi_consumer_cache:
                logger.info(f"Using cached contents for invoice {invoice_id}")
                consumers = self._multi_consumer_cache[invoice_id]
            else:
                # Click EDIT on this folder row to open it
                if not self.open_invoice_details(None, service_month_year=svc_month, svc_code=svc_code, invoice_id=invoice_id):
                    logger.warning(f"Could not open folder {invoice_id}")
                    return []

                # Now we're in the invoice view - scrape consumer lines
                consumers = self.cache_multi_consumer_invoice_contents(svc_code, svc_month, invoice_id=invoice_id)

            # Convert to inventory format
            invoices = []
            for c in consumers:
                name_parts = c.get('consumer_name', '').split(',')
                invoices.append({
                    'invoice_id': invoice_id,
                    'last_name': name_parts[0].strip() if name_parts else '',
                    'first_name': name_parts[1].strip() if len(name_parts) > 1 else '',
                    'uci': c.get('uci', ''),
                    'service_month': svc_month,
                    'svc_code': c.get('svc_code', svc_code)
                })

            logger.info(f"  Found {len(invoices)} consumers in folder")
            return invoices

        except Exception as e:
            logger.error(f"Error expanding folder: {e}")
            return []

    def cache_multi_consumer_invoice_contents(self, svc_code: str, svc_month_year: str, invoice_id: str = '') -> List[Dict]:
        """
        Scrape the currently open invoice view to cache all consumer lines.
        Called when we first enter a multi-consumer invoice to remember what's inside.

        Returns list of dicts: {line_number, consumer_name, uci, svc_code, svc_subcode, auth_number}
        """
        try:
            invoice_key = invoice_id or (svc_code, svc_month_year)

            # Check if already cached
            if invoice_key in self._multi_consumer_cache:
                logger.info(f"Using cached contents for invoice {invoice_key}")
                return self._multi_consumer_cache[invoice_key]

            logger.info(f"Caching multi-consumer invoice contents: invoice_id={invoice_id}, SVC={svc_code}, Month={svc_month_year}")

            consumers = self.page.evaluate('''() => {
                const results = [];
                const rows = document.querySelectorAll('tr');

                for (const row of rows) {
                    const cells = row.querySelectorAll('td');
                    if (cells.length < 6) continue;  // Skip header/insufficient rows

                    // Invoice view columns: [0]Checkbox, [1]Line#, [2]Consumer, [3]UCI#, [4]SVC Code, [5]SVC Subcode, [6]Auth#, ...
                    // Find Line# column - it's a small integer (1, 2, 3...)
                    let lineIdx = -1;
                    for (let i = 0; i < Math.min(cells.length, 3); i++) {
                        const text = cells[i]?.innerText?.trim() || '';
                        if (/^\\d{1,3}$/.test(text) && parseInt(text) < 100) {
                            lineIdx = i;
                            break;
                        }
                    }

                    if (lineIdx === -1) continue;

                    const lineNum = cells[lineIdx]?.innerText?.trim() || '';
                    const consumerName = cells[lineIdx + 1]?.innerText?.trim() || '';
                    const uci = cells[lineIdx + 2]?.innerText?.trim() || '';
                    const svcCode = cells[lineIdx + 3]?.innerText?.trim() || '';
                    const svcSubcode = cells[lineIdx + 4]?.innerText?.trim() || '';
                    const authNumber = cells[lineIdx + 5]?.innerText?.trim() || '';

                    // Validate: UCI should be a number, consumer name should have letters
                    if (lineNum && /^\\d+$/.test(lineNum) && /^\\d+$/.test(uci) && /[a-zA-Z]/.test(consumerName)) {
                        results.push({
                            line_number: parseInt(lineNum),
                            consumer_name: consumerName,
                            uci: uci,
                            svc_code: svcCode,
                            svc_subcode: svcSubcode,
                            auth_number: authNumber
                        });
                    }
                }
                return results;
            }''')

            self._multi_consumer_cache[invoice_key] = consumers
            logger.info(f"Cached {len(consumers)} consumer lines for invoice {invoice_key}")
            for c in consumers:
                logger.info(f"  Line {c['line_number']}: {c['consumer_name']} (UCI: {c['uci']})")

            return consumers

        except Exception as e:
            logger.error(f"Failed to cache multi-consumer invoice contents: {e}")
            return []

    def open_invoice_details(self, consumer_name: str, service_month_year: str = None, uci: str = None, svc_code: str = None, invoice_id: str = None) -> bool:
        """
        Click EDIT to open invoice details, matching by UCI + Service Code + Service M/Y.
        Falls back to multi-consumer invoice matching if direct match not found.
        """
        try:
            logger.info(f"Opening invoice: Invoice={invoice_id}, UCI={uci}, SVC={svc_code}, Month={service_month_year}")
            self._screenshot("09_before_edit_click")

            clicked = False

            # Method 0: Match by invoice_id (most precise — used by folder expansion)
            if invoice_id:
                result = self.page.evaluate(f'''() => {{
                    const rows = document.querySelectorAll('tr');
                    for (const row of rows) {{
                        const cells = row.querySelectorAll('td');
                        if (cells.length < 6) continue;
                        const rowInvoiceId = cells[1]?.innerText?.trim() || '';
                        if (rowInvoiceId === '{invoice_id}') {{
                            const editLink = row.querySelector('a[href*="edit"], a img, img[src*="edit"]');
                            if (editLink) {{
                                editLink.click();
                                return 'clicked invoice ' + rowInvoiceId;
                            }}
                            const lastCell = cells[cells.length - 1];
                            const editInLast = lastCell.querySelector('a, img');
                            if (editInLast) {{
                                editInLast.click();
                                return 'clicked invoice ' + rowInvoiceId + ' via last cell';
                            }}
                        }}
                    }}
                    return 'no match for invoice_id {invoice_id}';
                }}''')
                logger.info(f"Invoice ID match result: {result}")
                clicked = 'clicked' in result

            # Method 1: Find row matching UCI + Service Code + Service M/Y (single-consumer invoice)
            if not clicked and uci and service_month_year:
                svc = svc_code or ''
                result = self.page.evaluate(f'''() => {{
                    // Normalize month format: "8/2025" -> "08/2025"
                    const normalizeMonth = (m) => {{
                        if (!m) return '';
                        const parts = m.split('/');
                        if (parts.length === 2) {{
                            return parts[0].padStart(2, '0') + '/' + parts[1];
                        }}
                        return m;
                    }};
                    const targetMonth = normalizeMonth('{service_month_year}');

                    const rows = document.querySelectorAll('tr');
                    for (const row of rows) {{
                        const cells = row.querySelectorAll('td');
                        if (cells.length < 6) continue;

                        // Table: [0]Checkbox, [1]Invoice#, [2]Service Code, [3]Service M/Y, [4]UCI#, [5]Consumer Name, ...
                        const rowSvcCode = cells[2]?.innerText?.trim() || '';
                        const rowMonth = normalizeMonth(cells[3]?.innerText?.trim() || '');
                        const rowUci = cells[4]?.innerText?.trim() || '';

                        // Match all three: UCI, Service Code, Service M/Y
                        const matchesUci = rowUci === '{uci}';
                        const matchesMonth = rowMonth === targetMonth;
                        const matchesSvc = !'{svc}' || rowSvcCode === '{svc}' || rowSvcCode.startsWith('{svc}');

                        if (matchesUci && matchesMonth && matchesSvc) {{
                            const editLink = row.querySelector('a[href*="edit"], a img, img[src*="edit"]');
                            if (editLink) {{
                                editLink.click();
                                return 'clicked single-consumer: UCI={uci}, Month={service_month_year}';
                            }}
                            const lastCell = cells[cells.length - 1];
                            const editInLast = lastCell.querySelector('a, img');
                            if (editInLast) {{
                                editInLast.click();
                                return 'clicked edit in last cell';
                            }}
                        }}
                    }}
                    return 'no direct match';
                }}''')
                logger.info(f"Direct match result: {result}")
                clicked = 'clicked' in result

            # Method 2: Try multi-consumer invoice (no UCI in search, match by Service Code + Month)
            if not clicked and service_month_year:
                svc = svc_code or ''
                result = self.page.evaluate(f'''() => {{
                    // Normalize month format: "8/2025" -> "08/2025"
                    const normalizeMonth = (m) => {{
                        if (!m) return '';
                        const parts = m.split('/');
                        if (parts.length === 2) {{
                            return parts[0].padStart(2, '0') + '/' + parts[1];
                        }}
                        return m;
                    }};
                    const targetMonth = normalizeMonth('{service_month_year}');

                    const rows = document.querySelectorAll('tr');
                    for (const row of rows) {{
                        const cells = row.querySelectorAll('td');
                        if (cells.length < 6) continue;

                        // Table: [0]Checkbox, [1]Invoice#, [2]Service Code, [3]Service M/Y, [4]UCI#, [5]Consumer Name, ...
                        const rowSvcCode = cells[2]?.innerText?.trim() || '';
                        const rowMonth = normalizeMonth(cells[3]?.innerText?.trim() || '');
                        const rowUci = cells[4]?.innerText?.trim() || '';

                        // Multi-consumer: UCI is empty, but Service Code + Month match
                        const isMultiConsumer = !rowUci || rowUci === '';
                        const matchesMonth = rowMonth === targetMonth;
                        const matchesSvc = !'{svc}' || rowSvcCode === '{svc}' || rowSvcCode.startsWith('{svc}');

                        if (isMultiConsumer && matchesMonth && matchesSvc) {{
                            const editLink = row.querySelector('a[href*="edit"], a img, img[src*="edit"]');
                            if (editLink) {{
                                editLink.click();
                                return 'clicked multi-consumer: Month={service_month_year}';
                            }}
                            const lastCell = cells[cells.length - 1];
                            const editInLast = lastCell.querySelector('a, img');
                            if (editInLast) {{
                                editInLast.click();
                                return 'clicked multi-consumer edit';
                            }}
                        }}
                    }}
                    return 'no multi-consumer match';
                }}''')
                logger.info(f"Multi-consumer match result: {result}")
                clicked = 'clicked' in result

            # Method 3: Fall back to first EDIT link
            if not clicked:
                logger.warning("No matching invoice found, clicking first EDIT")
                edit_selectors = ['img[src*="edit" i]', 'a:has-text("EDIT")']
                for selector in edit_selectors:
                    try:
                        self.page.click(selector, timeout=2000)
                        clicked = True
                        logger.info(f"Fallback: clicked first {selector}")
                        break
                    except:
                        continue

            time.sleep(2)
            self.page.wait_for_load_state("networkidle")
            time.sleep(2)
            self._screenshot("10_after_edit_click")

            logger.info(f"Current URL after edit click: {self.page.url}")
            return clicked

        except Exception as e:
            logger.error(f"Failed to open invoice details: {e}")
            self._screenshot("error_edit_click")
            return False

    def open_calendar(self, uci: str, svc_code: str, svc_subcode: str, service_month: str) -> bool:
        """Click on Days Attend to open the calendar for a specific line"""
        try:
            logger.info(f"Opening calendar for UCI: {uci}, SVC: {svc_code}, Subcode: {svc_subcode}, Month: {service_month}")
            self._screenshot("11_before_calendar_click")

            # Find the row by UCI and click on Days Attend column
            # Days Attend is typically column 8 (0-indexed) based on the table structure
            clicked = self.page.evaluate(f'''() => {{
                const rows = document.querySelectorAll('tr');
                for (const row of rows) {{
                    const text = row.innerText;
                    // Match by UCI number
                    if (text.includes('{uci}')) {{
                        const cells = row.querySelectorAll('td');
                        // Days Attend column - try clicking it (usually column 8)
                        // The header row shows: Line#, Consumer, UCI#, SVC Code, SVC Subcode, Auth#, Auth Date, Unit Type, Units Billed, Days Attend, ...
                        if (cells.length >= 9) {{
                            // Days Attend is around index 8-9
                            for (let i = 7; i < Math.min(cells.length, 12); i++) {{
                                const cell = cells[i];
                                // Click on the Days Attend cell (it will be a number or link)
                                const link = cell.querySelector('a');
                                if (link) {{
                                    link.click();
                                    return 'clicked link in column ' + i;
                                }}
                            }}
                            // If no link found, try clicking cell 8 directly
                            if (cells[8]) {{
                                cells[8].click();
                                return 'clicked cell 8';
                            }}
                        }}
                    }}
                }}
                return 'not found';
            }}''')

            logger.info(f"Calendar click result: {clicked}")
            time.sleep(2)
            self.page.wait_for_load_state("networkidle")
            time.sleep(2)
            self._screenshot("12_after_calendar_click")

            current_url = self.page.url
            logger.info(f"URL after calendar click: {current_url}")

            if '/invoices/unitcalendar' in current_url or 'calendar' in current_url.lower():
                logger.info("Opened calendar page")
                return True

            # Check if page content changed
            if clicked != 'not found':
                return True

            logger.warning("May not have opened calendar")
            return False

        except Exception as e:
            logger.error(f"Failed to open calendar: {e}")
            self._screenshot("error_calendar")
            return False

    def enter_service_days(self, service_days: List[int], units_per_day: int = 1) -> bool:
        """Enter units for each service day in the calendar"""
        try:
            logger.info(f"Entering units for days: {service_days}")

            # The calendar has input fields for each day
            # Find inputs by their position in the calendar grid
            for day in service_days:
                # Find the input field for this day
                # Calendar inputs are typically identified by day number
                self.page.evaluate(f'''() => {{
                    // Find all cells in calendar
                    const cells = document.querySelectorAll('td');
                    for (const cell of cells) {{
                        // Look for cell containing the day number
                        const daySpan = cell.querySelector('span, div');
                        const input = cell.querySelector('input[type="text"]');

                        if (input) {{
                            // Check if this cell is for day {day}
                            const cellText = cell.innerText.trim();
                            if (cellText.startsWith('{day}') || cellText === '{day}') {{
                                input.value = '{units_per_day}';
                                input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                                return true;
                            }}
                        }}
                    }}

                    // Alternative: find input by looking at day numbers
                    const allInputs = document.querySelectorAll('input[type="text"]');
                    // Calendar typically has day numbers followed by input fields
                    return false;
                }}''')

            # Wait a moment for form to update
            time.sleep(1)
            logger.info(f"Entered units for {len(service_days)} days")
            return True

        except Exception as e:
            logger.error(f"Failed to enter service days: {e}")
            return False

    def enter_calendar_units(self, service_days: List[int], units_per_day: int = 1) -> Tuple[int, List[int], List[int]]:
        """
        Enter units in calendar input fields for specified days.
        Detects disabled/greyed-out inputs and inputs that already have values.

        Returns:
            Tuple of (days_entered, unavailable_days, already_entered_days)
            - days_entered: count of successfully entered days
            - unavailable_days: list of day numbers that were greyed out/disabled
            - already_entered_days: list of day numbers that already had values (skipped to prevent overwrite)
        """
        try:
            logger.info(f"Entering {units_per_day} unit(s) for days: {service_days}")

            days_entered = 0
            unavailable_days = []
            already_entered_days = []

            for day in service_days:
                try:
                    result = self.page.evaluate(f'''() => {{
                        // Look through all table cells
                        const cells = document.querySelectorAll('td');
                        for (const cell of cells) {{
                            const text = cell.innerText.trim();
                            const input = cell.querySelector('input');

                            // Check if cell contains our day number and has an input
                            if (text.includes('{day}') && input) {{
                                // Make sure it's the right day (not just contains the digit)
                                const lines = text.split('\\n');
                                if (lines[0].trim() === '{day}') {{
                                    // Check if input is disabled/readonly/greyed-out
                                    if (input.disabled || input.readOnly ||
                                        input.getAttribute('disabled') !== null ||
                                        input.getAttribute('readonly') !== null ||
                                        cell.classList.contains('disabled') ||
                                        getComputedStyle(input).pointerEvents === 'none' ||
                                        parseFloat(getComputedStyle(input).opacity) < 0.5) {{
                                        return 'disabled';
                                    }}
                                    // Check if already has a value (prevent overwrite)
                                    const existingValue = parseFloat(input.value) || 0;
                                    if (existingValue > 0) {{
                                        return 'already_entered';
                                    }}
                                    // Enter value
                                    input.value = '{units_per_day}';
                                    input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                    input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                                    return 'success';
                                }}
                            }}
                        }}
                        return 'not_found';
                    }}''')

                    if result == 'success':
                        days_entered += 1
                        logger.info(f"  Entered unit for day {day}")
                    elif result == 'already_entered':
                        already_entered_days.append(day)
                        logger.info(f"  Day {day} already has a value (skipped)")
                    elif result == 'disabled':
                        unavailable_days.append(day)
                        logger.warning(f"  Day {day} is greyed out/disabled")
                    else:
                        unavailable_days.append(day)
                        logger.warning(f"  Could not find input for day {day}")

                except Exception as e:
                    unavailable_days.append(day)
                    logger.warning(f"  Error entering day {day}: {e}")

            time.sleep(1)
            return days_entered, unavailable_days, already_entered_days

        except Exception as e:
            logger.error(f"Calendar entry failed: {e}")
            return 0, list(service_days), []

    def click_update(self) -> bool:
        """Click Update button to save calendar entries, then Close to exit"""
        try:
            logger.info("Clicking Update...")
            self._js_click("Update")
            self.page.wait_for_load_state("networkidle")
            time.sleep(2)
            logger.info("Update clicked")

            # Click Close to exit calendar view and return to invoice view
            logger.info("Clicking Close...")
            self._js_click("Close")
            self.page.wait_for_load_state("networkidle")
            time.sleep(1)
            logger.info("Close clicked")

            return True
        except Exception as e:
            logger.error(f"Update failed: {e}")
            return False

    def capture_portal_billing_data(self, uci: str) -> dict:
        """
        Capture billing data from the calendar page's Invoice Line Summary section.
        Returns dict with: units_billed, gross_amount, net_amount, unit_rate
        """
        try:
            time.sleep(1)  # Wait for values to populate after entering units
            self._screenshot("13_capture_billing_data")

            # Scrape from Invoice Line Summary section on calendar page
            billing_data = self.page.evaluate('''() => {
                let units = 0, rate = 0, gross = 0, net = 0;

                // Get all text content to find values by labels
                const pageText = document.body.innerText;

                // Extract Unit Rate (shown as text like "118.940")
                const rateMatch = pageText.match(/Unit Rate[:\\s]+([\\d.]+)/i);
                if (rateMatch) rate = parseFloat(rateMatch[1]) || 0;

                // Find all input fields on the page
                const inputs = document.querySelectorAll('input');
                for (const input of inputs) {
                    const name = (input.name || '').toLowerCase();
                    const id = (input.id || '').toLowerCase();
                    const val = parseFloat((input.value || '0').replace(/[$,]/g, '')) || 0;

                    // Total Units input
                    if (name.includes('totalunit') || id.includes('totalunit') ||
                        name.includes('total_unit') || id.includes('total_unit')) {
                        if (val > 0) units = val;
                    }
                    // Gross Amount input
                    if (name.includes('gross') || id.includes('gross')) {
                        if (val > 0) gross = val;
                    }
                    // Net Amount input
                    if (name.includes('net') || id.includes('net')) {
                        if (val > 0) net = val;
                    }
                }

                // Alternative: Look for labeled fields in table structure
                const tds = document.querySelectorAll('td');
                for (let i = 0; i < tds.length; i++) {
                    const text = tds[i].innerText.trim();
                    const nextTd = tds[i + 1];
                    if (!nextTd) continue;

                    // Check for input in next cell or text value
                    const nextInput = nextTd.querySelector('input');
                    const val = nextInput ?
                        parseFloat((nextInput.value || '0').replace(/[$,]/g, '')) || 0 :
                        parseFloat(nextTd.innerText.replace(/[$,]/g, '')) || 0;

                    if (text.includes('Total Units') && val > 0) units = val;
                    if (text.includes('Unit Rate') && val > 0) rate = val;
                    if (text.includes('Gross Amount') && val > 0) gross = val;
                    if (text.includes('Net Amount') && val > 0) net = val;
                }

                return { units_billed: units, unit_rate: rate, gross_amount: gross, net_amount: net };
            }''')

            if billing_data:
                units = billing_data.get('units_billed', 0)
                rate = billing_data.get('unit_rate', 0)
                gross = billing_data.get('gross_amount', 0)
                net = billing_data.get('net_amount', 0)

                logger.info(f"Captured: Units={units}, Rate={rate}, Gross={gross}, Net={net}")
                return {
                    'units_billed': units,
                    'gross_amount': gross,
                    'net_amount': net,
                    'unit_rate': rate
                }
            else:
                logger.warning("Could not capture billing data from calendar")
                return {'units_billed': 0, 'gross_amount': 0, 'net_amount': 0, 'unit_rate': 0}

        except Exception as e:
            logger.error(f"Error capturing billing data: {e}")
            return {'units_billed': 0, 'gross_amount': 0, 'net_amount': 0, 'unit_rate': 0}

    def submit_billing_record(self, record: Dict) -> SubmissionResult:
        """
        Submit a single billing record to the portal.

        Args:
            record: Dictionary containing:
                - uci: UCI number
                - consumer_name: Consumer's name
                - lastname, firstname: Name parts
                - auth_number: Authorization number
                - svc_code: Service code
                - svc_subcode: Service subcode
                - service_days: List of day numbers [3, 10, 17, ...]
                - entered_units: Units from CSV invoice
                - entered_amount: Amount from CSV invoice

        Returns:
            SubmissionResult with success status and billing data
        """
        try:
            uci = record.get('uci', '')
            consumer_name = record.get('consumer_name', '')
            lastname = record.get('lastname', '')
            svc_code = record.get('svc_code', '')
            svc_subcode = record.get('svc_subcode', '')
            service_month = record.get('service_month', '')  # MM/YYYY format (e.g., "08/2025")
            service_days = record.get('service_days', [])
            # Get CSV invoice billing data
            invoice_units = float(record.get('entered_units', 0) or 0)
            invoice_amount = float(record.get('entered_amount', 0) or 0)

            logger.info(f"Processing: {consumer_name} (UCI: {uci}, SVC: {svc_code}, Month: {service_month})")

            # Open invoice details - match by UCI + Service Code + Service M/Y
            if not self.open_invoice_details(lastname, service_month_year=service_month, uci=uci, svc_code=svc_code):
                return SubmissionResult(
                    success=False,
                    consumer_name=consumer_name,
                    uci=uci,
                    error_message="Could not open invoice details",
                    invoice_units=invoice_units,
                    invoice_amount=invoice_amount
                )

            # Open calendar
            if not self.open_calendar(uci, svc_code, svc_subcode, service_month):
                return SubmissionResult(
                    success=False,
                    consumer_name=consumer_name,
                    uci=uci,
                    days_expected=len(service_days),
                    error_message="Could not open calendar",
                    invoice_units=invoice_units,
                    invoice_amount=invoice_amount
                )

            # Enter service days
            days_entered, unavailable_days, already_entered_days = self.enter_calendar_units(service_days)

            # Determine success level
            # effective_days = newly entered + already had values
            days_expected = len(service_days)
            effective_days = days_entered + len(already_entered_days)
            is_partial = effective_days > 0 and effective_days < days_expected
            is_success = effective_days == days_expected and days_expected > 0

            # Capture billing data from calendar's Invoice Line Summary (before Update)
            portal_data = self.capture_portal_billing_data(uci)

            # Click Update
            if not self.click_update():
                return SubmissionResult(
                    success=False,
                    partial=is_partial,
                    consumer_name=consumer_name,
                    uci=uci,
                    days_entered=days_entered,
                    days_expected=days_expected,
                    unavailable_days=unavailable_days,
                    already_entered_days=already_entered_days,
                    error_message="Could not click Update",
                    invoice_units=invoice_units,
                    invoice_amount=invoice_amount
                )

            # Determine error message based on outcome
            if is_partial:
                error_msg = f"PARTIAL: Only {effective_days}/{days_expected} days covered. Unavailable: {unavailable_days}"
                logger.warning(f"⚠ Partial: {consumer_name} ({effective_days}/{days_expected} days)")
            elif effective_days == 0:
                error_msg = f"FAILED: No days could be entered - all {days_expected} days unavailable"
                logger.error(f"✗ Failed: {consumer_name} - all days unavailable")
            else:
                if already_entered_days:
                    logger.info(f"✓ Submitted: {consumer_name} ({days_entered} new, {len(already_entered_days)} already entered)")
                else:
                    logger.info(f"✓ Submitted: {consumer_name} ({days_entered} days)")
                error_msg = None

            return SubmissionResult(
                success=is_success,
                partial=is_partial,
                consumer_name=consumer_name,
                uci=uci,
                days_entered=days_entered,
                days_expected=days_expected,
                unavailable_days=unavailable_days,
                already_entered_days=already_entered_days,
                error_message=error_msg,
                rc_units_billed=portal_data.get('units_billed', 0),
                rc_gross_amount=portal_data.get('gross_amount', 0),
                rc_net_amount=portal_data.get('net_amount', 0),
                rc_unit_rate=portal_data.get('unit_rate', 0),
                invoice_units=invoice_units,
                invoice_amount=invoice_amount
            )

        except Exception as e:
            logger.error(f"Submission error: {e}")
            return SubmissionResult(
                success=False,
                consumer_name=record.get('consumer_name', ''),
                uci=record.get('uci', ''),
                days_expected=len(record.get('service_days', [])),
                error_message=str(e),
                invoice_units=float(record.get('entered_units', 0) or 0),
                invoice_amount=float(record.get('entered_amount', 0) or 0)
            )

    def submit_billing_record_in_open_invoice(self, record: Dict) -> SubmissionResult:
        """
        Submit a billing record when the invoice is already open.
        Skips opening the invoice and goes directly to opening the calendar.

        This is used for subsequent records in the same invoice after the first one.
        After click_update(), the page returns to invoice view, so we stay there.
        """
        try:
            uci = record.get('uci', '')
            consumer_name = record.get('consumer_name', '')
            svc_code = record.get('svc_code', '')
            svc_subcode = record.get('svc_subcode', '')
            service_month = record.get('service_month', '')
            svc_month_year = record.get('svc_month_year', '')
            service_days = record.get('service_days', [])
            invoice_units = float(record.get('entered_units', 0) or 0)
            invoice_amount = float(record.get('entered_amount', 0) or 0)

            logger.info(f"Processing (in-invoice): {consumer_name} (UCI: {uci})")

            # Open calendar directly - invoice is already open
            if not self.open_calendar(uci, svc_code, svc_subcode, service_month):
                return SubmissionResult(
                    success=False,
                    consumer_name=consumer_name,
                    uci=uci,
                    days_expected=len(service_days),
                    error_message="Could not open calendar (invoice already open)",
                    invoice_units=invoice_units,
                    invoice_amount=invoice_amount
                )

            # Enter service days
            days_entered, unavailable_days, already_entered_days = self.enter_calendar_units(service_days)

            # Determine success level
            # effective_days = newly entered + already had values
            days_expected = len(service_days)
            effective_days = days_entered + len(already_entered_days)
            is_partial = effective_days > 0 and effective_days < days_expected
            is_success = effective_days == days_expected and days_expected > 0

            # Capture billing data
            portal_data = self.capture_portal_billing_data(uci)

            # Click Update - returns to invoice view
            if not self.click_update():
                return SubmissionResult(
                    success=False,
                    partial=is_partial,
                    consumer_name=consumer_name,
                    uci=uci,
                    days_entered=days_entered,
                    days_expected=days_expected,
                    unavailable_days=unavailable_days,
                    already_entered_days=already_entered_days,
                    error_message="Could not click Update",
                    invoice_units=invoice_units,
                    invoice_amount=invoice_amount
                )

            # Determine error message based on outcome
            if is_partial:
                error_msg = f"PARTIAL: Only {effective_days}/{days_expected} days covered. Unavailable: {unavailable_days}"
                logger.warning(f"⚠ Partial (in-invoice): {consumer_name} ({effective_days}/{days_expected} days)")
            elif effective_days == 0:
                error_msg = f"FAILED: No days could be entered - all {days_expected} days unavailable"
                logger.error(f"✗ Failed (in-invoice): {consumer_name} - all days unavailable")
            else:
                if already_entered_days:
                    logger.info(f"✓ Submitted (in-invoice): {consumer_name} ({days_entered} new, {len(already_entered_days)} already entered)")
                else:
                    logger.info(f"✓ Submitted (in-invoice): {consumer_name} ({days_entered} days)")
                error_msg = None

            return SubmissionResult(
                success=is_success,
                partial=is_partial,
                consumer_name=consumer_name,
                uci=uci,
                days_entered=days_entered,
                days_expected=days_expected,
                unavailable_days=unavailable_days,
                already_entered_days=already_entered_days,
                error_message=error_msg,
                rc_units_billed=portal_data.get('units_billed', 0),
                rc_gross_amount=portal_data.get('gross_amount', 0),
                rc_net_amount=portal_data.get('net_amount', 0),
                rc_unit_rate=portal_data.get('unit_rate', 0),
                invoice_units=invoice_units,
                invoice_amount=invoice_amount
            )

        except Exception as e:
            logger.error(f"In-invoice submission error: {e}")
            return SubmissionResult(
                success=False,
                consumer_name=record.get('consumer_name', ''),
                uci=record.get('uci', ''),
                days_expected=len(record.get('service_days', [])),
                error_message=str(e),
                invoice_units=float(record.get('entered_units', 0) or 0),
                invoice_amount=float(record.get('entered_amount', 0) or 0)
            )

    def _group_records_by_invoice(self, records: List[Dict]) -> Dict[tuple, List[Dict]]:
        """
        Group billing records by invoice key (svc_code, service_month).
        Records with the same key belong to the same invoice.

        Returns: Dict mapping (svc_code, service_month) -> [list of records]
        """
        grouped = defaultdict(list)
        for record in records:
            svc_code = record.get('svc_code', '')
            service_month = record.get('service_month', '')  # MM/YYYY format
            invoice_key = (svc_code, service_month)
            grouped[invoice_key].append(record)

        logger.info(f"Grouped {len(records)} records into {len(grouped)} invoice groups")
        for key, recs in grouped.items():
            logger.info(f"  Invoice SVC={key[0]}, Month={key[1]}: {len(recs)} records")

        return dict(grouped)

    def submit_all_records(self, records: List[Dict], provider_name: str = None) -> List[SubmissionResult]:
        """
        Submit all billing records with inventory-first approach.
        First scrapes all available invoices from portal, matches CSV records against inventory,
        then only processes records that have matching invoices.

        Args:
            records: List of billing record dictionaries
            provider_name: Name of the service provider (if None, uses spn_id from first record)

        Returns:
            List of SubmissionResult objects (includes skipped records with SKIPPED: prefix in error_message)
        """
        results = []

        # Login
        if not self.login():
            return [SubmissionResult(success=False, error_message="Login failed")]

        # Get provider from first record's spn_id if not specified
        if not provider_name and records:
            provider_name = records[0].get('spn_id', '')
        if not provider_name:
            return [SubmissionResult(success=False, error_message="No provider specified")]

        # Select provider
        if not self.select_provider(provider_name):
            return [SubmissionResult(success=False, error_message=f"Provider selection failed: {provider_name}")]

        # Navigate to invoices and search
        if not self.navigate_to_invoices():
            return [SubmissionResult(success=False, error_message="Navigation failed")]

        # === INVENTORY-FIRST PHASE ===
        logger.info("=" * 60)
        logger.info("=== PHASE 1: Building Invoice Inventory ===")
        logger.info("=" * 60)
        self._invoice_search_cache = self.scrape_all_invoice_pages()
        logger.info(f"Inventory complete: {len(self._invoice_search_cache)} invoices available on portal")

        # === MATCHING PHASE ===
        logger.info("=" * 60)
        logger.info("=== PHASE 2: Matching Records to Inventory ===")
        logger.info("=" * 60)
        matchable_records, unmatched_records = self.match_records_to_inventory(records, self._invoice_search_cache)
        logger.info(f"Match results: {len(matchable_records)} matchable, {len(unmatched_records)} will be skipped")

        # Create skip results for unmatched records
        for record in unmatched_records:
            results.append(SubmissionResult(
                success=False,
                consumer_name=record.get('consumer_name', ''),
                uci=record.get('uci', ''),
                error_message=f"SKIPPED: {record.get('skip_reason', 'No matching invoice on portal')}",
                invoice_units=float(record.get('entered_units', 0) or 0),
                invoice_amount=float(record.get('entered_amount', 0) or 0)
            ))

        if not matchable_records:
            logger.warning("No records matched any invoices in inventory - nothing to process")
            return results

        # === PROCESSING PHASE ===
        logger.info("=" * 60)
        logger.info(f"=== PHASE 3: Processing {len(matchable_records)} Matched Records ===")
        logger.info("=" * 60)

        # Navigate back to search results (may have changed due to pagination)
        self.navigate_to_invoices()

        # Group matchable records by invoice key for efficient batch processing
        grouped_records = self._group_records_by_invoice(matchable_records)

        # Process each invoice group
        for invoice_key, invoice_records in grouped_records.items():
            svc_code, service_month = invoice_key
            logger.info(f"=== Processing invoice group: SVC={svc_code}, Month={service_month} ({len(invoice_records)} records) ===")

            # Track if this is the first record in the invoice
            is_first_record = True
            invoice_opened_successfully = False

            for record in invoice_records:
                if is_first_record:
                    # First record: Open invoice from search, then process
                    result = self.submit_billing_record(record)
                    is_first_record = False
                    invoice_opened_successfully = result.success or 'Could not open invoice' not in (result.error_message or '')

                    # After first record, cache multi-consumer contents if applicable
                    if invoice_opened_successfully:
                        # Check if this is a multi-consumer invoice (empty UCI in search results)
                        normalized_month = self._normalize_month(service_month)
                        search_match = next(
                            (inv for inv in self._invoice_search_cache
                             if inv.get('svc_code') == svc_code and self._normalize_month(inv.get('svc_month', '')) == normalized_month),
                            None
                        )
                        if search_match and not search_match.get('has_uci', True):
                            # This is a multi-consumer invoice - cache contents for efficiency
                            self.cache_multi_consumer_invoice_contents(svc_code, service_month, invoice_id=search_match.get('invoice_id', ''))
                else:
                    # Subsequent records: Invoice is already open, skip navigation
                    if invoice_opened_successfully:
                        result = self.submit_billing_record_in_open_invoice(record)
                    else:
                        # Invoice failed to open on first attempt, skip remaining records in group
                        result = SubmissionResult(
                            success=False,
                            consumer_name=record.get('consumer_name', ''),
                            uci=record.get('uci', ''),
                            error_message="Skipped - invoice failed to open",
                            invoice_units=float(record.get('entered_units', 0) or 0),
                            invoice_amount=float(record.get('entered_amount', 0) or 0)
                        )

                results.append(result)

                if result.success:
                    logger.info(f"✓ Submitted: {result.consumer_name} ({result.days_entered} days)")
                else:
                    logger.error(f"✗ Failed: {result.consumer_name} - {result.error_message}")

            # After processing all records in this invoice, navigate back to search
            logger.info(f"=== Finished invoice group: SVC={svc_code}, Month={service_month} ===")
            try:
                # Click Invoices tab
                logger.info("Navigating back to Invoices tab...")
                self.page.click('a:has-text("Invoices")', timeout=3000)
                self.page.wait_for_load_state("networkidle")
                time.sleep(1)

                # Click Search button - try multiple methods for reliability
                logger.info("Clicking Search to refresh results...")
                search_clicked = False

                # Method 1: Direct button selector
                try:
                    self.page.click('button:has-text("Search")', timeout=2000)
                    search_clicked = True
                    logger.info("Search clicked via button selector")
                except:
                    pass

                # Method 2: Input button
                if not search_clicked:
                    try:
                        self.page.click('input[value="Search"]', timeout=2000)
                        search_clicked = True
                        logger.info("Search clicked via input selector")
                    except:
                        pass

                # Method 3: JavaScript fallback
                if not search_clicked:
                    self._js_click("Search")
                    logger.info("Search clicked via JavaScript")

                self.page.wait_for_load_state("networkidle")
                time.sleep(2)
                self._screenshot("14_back_to_search")

            except Exception as e:
                logger.warning(f"Failed to navigate back to search: {e}")

            # Clear current invoice tracking
            self._current_invoice_key = None

        # Clear caches at end of session
        self._invoice_search_cache = []
        self._multi_consumer_cache = {}

        return results


def submit_to_ebilling(records: List[Dict], username: str, password: str,
                       provider_name: str = None,
                       regional_center: str = "ELARC",
                       portal_url: str = None) -> List[SubmissionResult]:
    """
    Convenience function to submit billing records.

    Args:
        records: List of billing record dictionaries from CSV parser
        username: Portal username
        password: Portal password
        provider_name: Service provider name (if None, uses spn_id from first record)
        regional_center: Regional center code (ELARC, SGPRC, etc.)
        portal_url: Direct URL to the eBilling portal login page

    Returns:
        List of SubmissionResult objects
    """
    with DDSeBillingBot(username, password, headless=None,
                        regional_center=regional_center, portal_url=portal_url) as bot:
        return bot.submit_all_records(records, provider_name)


def scrape_invoice_inventory(username: str, password: str,
                             regional_center: str = "ELARC",
                             portal_url: str = None,
                             provider_id: str = None) -> Dict:
    """
    Scrape all available invoices from portal without submitting anything.
    Returns structured dict with status and invoice records.

    For multi-consumer folders (no UCI in search), clicks into each to get
    individual consumer invoices.

    Args:
        username: Portal username
        password: Portal password
        regional_center: Regional center code (ELARC, SGPRC, etc.)
        portal_url: Direct URL to the eBilling portal login page
        provider_id: Provider ID to select (if None, selects first available)

    Returns:
        Dict with keys:
            status: 'success' or 'error'
            invoices: List of invoice dicts (on success)
            error: Error type string (on error)
            message: Human-readable error message (on error)
    """
    with DDSeBillingBot(username, password, headless=None,
                        regional_center=regional_center, portal_url=portal_url) as bot:
        if not bot.login():
            logger.error("Login failed for inventory scrape")
            return {
                'status': 'error',
                'error': 'login_failed',
                'message': 'Could not log in to the RC portal. Check your username and password in Settings.'
            }

        password_expiry_days = bot.password_expiry_days

        # Select provider (use provided ID or first available)
        if provider_id:
            if not bot.select_provider(provider_id):
                logger.error(f"Could not select provider: {provider_id}")
                return {
                    'status': 'error',
                    'error': 'provider_not_found',
                    'message': f'Provider "{provider_id}" is not associated with this login on the RC portal. Check your SPN ID in Settings.'
                }
        else:
            if not bot.select_first_provider():
                logger.error("Could not select first provider")
                return {
                    'status': 'error',
                    'error': 'provider_not_found',
                    'message': 'No providers found on the RC portal for this login.'
                }

        if not bot.navigate_to_invoices():
            logger.error("Could not navigate to invoices")
            return {
                'status': 'error',
                'error': 'navigation_failed',
                'message': 'Could not navigate to the Invoices page on the RC portal.'
            }

        # Scrape search results (includes both single-consumer and folder rows)
        search_results = bot.scrape_all_invoice_pages()

        logger.info(f"=== Search Results: Found {len(search_results)} invoice rows ===")
        if not search_results:
            logger.warning("No invoices found in search results!")
            bot._screenshot("no_invoices_final")
            result = {'status': 'success', 'invoices': []}
            if password_expiry_days is not None:
                result['warnings'] = [f'Your eBilling portal password will expire in {password_expiry_days} days. Please change it soon.']
            return result

        for inv in search_results:
            logger.info(f"  Found: Invoice#{inv.get('invoice_id')} SVC={inv.get('svc_code')} "
                       f"Month={inv.get('svc_month')} UCI={inv.get('uci') or '(multi-consumer)'}")

        expanded_invoices = []

        for inv in search_results:
            if inv.get('has_uci'):
                # Single consumer invoice - parse name directly
                name_parts = inv.get('consumer_name', '').split(',')
                expanded_invoices.append({
                    'invoice_id': inv.get('invoice_id', ''),
                    'last_name': name_parts[0].strip() if name_parts else '',
                    'first_name': name_parts[1].strip() if len(name_parts) > 1 else '',
                    'uci': inv.get('uci', ''),
                    'service_month': inv.get('svc_month', ''),
                    'svc_code': inv.get('svc_code', '')
                })
            else:
                # Multi-consumer FOLDER - click into it to get individual invoices
                folder_invoices = bot.expand_multi_consumer_folder(inv)
                expanded_invoices.extend(folder_invoices)

                # Navigate back to search results for next folder
                bot.navigate_to_invoices()

        logger.info(f"Inventory scrape complete: {len(expanded_invoices)} total invoices")
        result = {'status': 'success', 'invoices': expanded_invoices}
        if password_expiry_days is not None:
            result['warnings'] = [f'Your eBilling portal password will expire in {password_expiry_days} days. Please change it soon.']
        return result


def get_portal_providers(username: str, password: str,
                         regional_center: str = "ELARC",
                         portal_url: str = None) -> Dict:
    """
    Log in to the RC portal and scrape the list of providers available for this login.

    Returns:
        Dict with keys:
            status: 'success' or 'error'
            providers: List of {'spn_id': 'XX1234', 'name': '...'} dicts (on success)
            error/message: Error info (on error)
    """
    with DDSeBillingBot(username, password, headless=None,
                        regional_center=regional_center, portal_url=portal_url) as bot:
        if not bot.login():
            return {
                'status': 'error',
                'error': 'login_failed',
                'message': 'Could not log in to the RC portal. Check your username and password in Settings.'
            }

        password_expiry_days = bot.password_expiry_days

        providers = bot.get_available_providers()

        if not providers:
            return {
                'status': 'error',
                'error': 'no_providers',
                'message': 'No providers found on the RC portal for this login.'
            }

        result = {'status': 'success', 'providers': providers}
        if password_expiry_days is not None:
            result['warnings'] = [f'Your eBilling portal password will expire in {password_expiry_days} days. Please change it soon.']
        return result


def scrape_all_providers_inventory(username: str, password: str,
                                   regional_center: str = "ELARC",
                                   portal_url: str = None) -> Dict:
    """
    Scan all providers on an RC login and return combined invoice inventory.

    Uses a single login session to iterate through all providers, avoiding
    multiple logins that trigger portal session conflicts.

    Returns:
        Dict with keys:
            status: 'success' or 'error'
            invoices: Combined list of invoice dicts, each tagged with 'provider_spn'
            providers_scanned: List of {'spn_id': ..., 'name': ...} that were scanned
            error/message: Error info (on error)
    """
    with DDSeBillingBot(username, password, headless=None,
                        regional_center=regional_center, portal_url=portal_url) as bot:
        if not bot.login():
            return {
                'status': 'error',
                'error': 'login_failed',
                'message': 'Could not log in to the RC portal. Check your username and password in Settings.'
            }
        password_expiry_days = bot.password_expiry_days

        providers = bot.get_available_providers()
        if not providers:
            return {
                'status': 'error',
                'error': 'no_providers',
                'message': 'No providers found on the RC portal for this login.'
            }

        all_invoices = []
        providers_scanned = []

        for i, prov in enumerate(providers):
            spn_id = prov['spn_id']
            logger.info(f"Scanning provider {spn_id} ({prov['name']})...")

            if not bot.select_provider(spn_id):
                logger.warning(f"Could not select provider {spn_id}, skipping")
                providers_scanned.append(prov)
                bot.navigate_to_provider_selection()
                continue

            if not bot.navigate_to_invoices():
                logger.warning(f"Could not navigate to invoices for {spn_id}, skipping")
                providers_scanned.append(prov)
                bot.navigate_to_provider_selection()
                continue

            # Scrape search results (includes both single-consumer and folder rows)
            search_results = bot.scrape_all_invoice_pages()
            logger.info(f"  Search results: {len(search_results)} invoice rows for {spn_id}")

            expanded_invoices = []
            for inv in search_results:
                if inv.get('has_uci'):
                    # Single consumer invoice - parse name directly
                    name_parts = inv.get('consumer_name', '').split(',')
                    expanded_invoices.append({
                        'invoice_id': inv.get('invoice_id', ''),
                        'last_name': name_parts[0].strip() if name_parts else '',
                        'first_name': name_parts[1].strip() if len(name_parts) > 1 else '',
                        'uci': inv.get('uci', ''),
                        'service_month': inv.get('svc_month', ''),
                        'svc_code': inv.get('svc_code', '')
                    })
                else:
                    # Multi-consumer FOLDER - click into it to get individual invoices
                    folder_invoices = bot.expand_multi_consumer_folder(inv)
                    expanded_invoices.extend(folder_invoices)
                    # Navigate back to search results for next folder
                    bot.navigate_to_invoices()

            # Tag each invoice with provider info
            for inv in expanded_invoices:
                inv['provider_spn'] = spn_id
                inv['provider_name'] = prov['name']
            all_invoices.extend(expanded_invoices)
            providers_scanned.append(prov)
            logger.info(f"  Found {len(expanded_invoices)} invoices for {spn_id}")

            # Navigate back to provider selection for next provider
            if i < len(providers) - 1:
                if not bot.navigate_to_provider_selection():
                    logger.warning(f"Failed to navigate back after {spn_id}, retrying...")
                    # Retry once — click Home then Dashboard
                    time.sleep(2)
                    if not bot.navigate_to_provider_selection():
                        logger.error(f"Cannot navigate back to provider selection after {spn_id}")
                        break

    logger.info(f"All-providers scan complete: {len(all_invoices)} total invoices from {len(providers_scanned)} providers")
    result = {
        'status': 'success',
        'invoices': all_invoices,
        'providers_scanned': providers_scanned
    }
    if password_expiry_days is not None:
        result['warnings'] = [f'Your eBilling portal password will expire in {password_expiry_days} days. Please change it soon.']
    return result
