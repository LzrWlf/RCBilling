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
from typing import List, Optional, Dict
import logging
import time
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    consumer_name: str = ""
    uci: str = ""
    days_entered: int = 0
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

    def __init__(self, username: str, password: str, headless: bool = False,
                 regional_center: str = 'ELARC', portal_url: str = None):
        self.username = username
        self.password = password
        self.headless = headless
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

        # Invoice caching for efficient multi-record processing
        self._invoice_search_cache: List[Dict] = []  # Level 1: Search results
        self._multi_consumer_cache: Dict[tuple, List[Dict]] = {}  # Level 2: Contents inside multi-consumer invoices
        self._current_invoice_key: Optional[tuple] = None  # Track currently open invoice (svc_code, svc_month_year)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def start(self):
        """Start browser session"""
        logger.info("Starting browser...")
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            channel='chrome',
            args=['--disable-blink-features=AutomationControlled']
        )
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.page.set_viewport_size({"width": 1400, "height": 900})
        logger.info("Browser started")

    def stop(self):
        """Close browser session"""
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        logger.info("Browser closed")

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

            # Accept agreement using JavaScript
            logger.info("Checking for user agreement...")
            time.sleep(2)
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
            self.page.wait_for_load_state("networkidle")
            time.sleep(2)
            self._screenshot("05_after_agreement")

            logger.info("Login successful")
            return True

        except Exception as e:
            logger.error(f"Login failed: {e}")
            self._screenshot("error_login_exception")
            return False

    def select_provider(self, provider_identifier: str) -> bool:
        """Select the service provider by SPN ID or name"""
        try:
            logger.info(f"Selecting provider: {provider_identifier}")
            self._screenshot("05_provider_selection")

            clicked = False

            # Method 1: Try exact SPN ID match first (e.g., PE1234)
            # SPN IDs appear in a specific column in the provider table
            clicked = self.page.evaluate(f'''() => {{
                const rows = document.querySelectorAll('tr');
                for (const row of rows) {{
                    const cells = row.querySelectorAll('td');
                    for (const cell of cells) {{
                        const text = cell.innerText.trim();
                        // Match SPN ID exactly (case-insensitive)
                        if (text.toUpperCase() === '{provider_identifier}'.toUpperCase()) {{
                            row.click();
                            return true;
                        }}
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
                        const rows = document.querySelectorAll('tr');
                        for (const row of rows) {{
                            const cells = row.querySelectorAll('td');
                            for (const cell of cells) {{
                                const text = cell.innerText.trim();
                                // Match if cell ends with the numeric portion
                                if (text.match(/[A-Za-z]+{numeric_part}$/i)) {{
                                    row.click();
                                    return true;
                                }}
                            }}
                        }}
                        return false;
                    }}''')
                    if clicked:
                        logger.info(f"Selected provider by numeric match: {numeric_part}")

            # Method 2: Fall back to provider name text match
            if not clicked:
                clicked = self.page.evaluate(f'''() => {{
                    const rows = document.querySelectorAll('tr');
                    for (const row of rows) {{
                        if (row.innerText.includes('{provider_identifier}')) {{
                            row.click();
                            return true;
                        }}
                    }}
                    // If exact match not found, try partial match
                    for (const row of rows) {{
                        if (row.innerText.toLowerCase().includes('{provider_identifier}'.toLowerCase())) {{
                            row.click();
                            return true;
                        }}
                    }}
                    return false;
                }}''')

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
            time.sleep(3)
            self._screenshot("08_after_search")

            return True
        except Exception as e:
            logger.error(f"Navigation failed: {e}")
            self._screenshot("error_navigation")
            return False

    def cache_invoice_search_results(self) -> list:
        """
        Scrape invoice search results table and return list of invoice info.
        Returns list of dicts: {invoice_id, svc_code, svc_month, uci, consumer_name, row_index}
        """
        try:
            invoices = self.page.evaluate('''() => {
                const results = [];
                const rows = document.querySelectorAll('tr');
                let rowIndex = 0;

                for (const row of rows) {
                    const cells = row.querySelectorAll('td');
                    if (cells.length < 5) continue;  // Skip header/empty rows

                    // Table columns: Invoice #, Service Code, Service M/Y, UCI#, Consumer Name, ...
                    const invoiceId = cells[0]?.innerText?.trim() || '';
                    const svcCode = cells[1]?.innerText?.trim() || '';
                    const svcMonth = cells[2]?.innerText?.trim() || '';
                    const uci = cells[3]?.innerText?.trim() || '';
                    const consumerName = cells[4]?.innerText?.trim() || '';

                    // Skip if no invoice ID (likely header row)
                    if (invoiceId && /^\d+$/.test(invoiceId)) {
                        results.push({
                            invoice_id: invoiceId,
                            svc_code: svcCode,
                            svc_month: svcMonth,
                            uci: uci,
                            consumer_name: consumerName,
                            row_index: rowIndex,
                            has_uci: uci.length > 0
                        });
                    }
                    rowIndex++;
                }
                return results;
            }''')

            logger.info(f"Cached {len(invoices)} invoice rows from search results")
            for inv in invoices:
                logger.debug(f"  Invoice {inv['invoice_id']}: SVC={inv['svc_code']}, Month={inv['svc_month']}, UCI={inv['uci'] or '(multi)'}")

            return invoices

        except Exception as e:
            logger.error(f"Failed to cache invoice search results: {e}")
            return []

    def cache_multi_consumer_invoice_contents(self, svc_code: str, svc_month_year: str) -> List[Dict]:
        """
        Scrape the currently open invoice view to cache all consumer lines.
        Called when we first enter a multi-consumer invoice to remember what's inside.

        Returns list of dicts: {line_number, consumer_name, uci, svc_code, svc_subcode, auth_number}
        """
        try:
            invoice_key = (svc_code, svc_month_year)

            # Check if already cached
            if invoice_key in self._multi_consumer_cache:
                logger.info(f"Using cached contents for invoice {invoice_key}")
                return self._multi_consumer_cache[invoice_key]

            logger.info(f"Caching multi-consumer invoice contents: SVC={svc_code}, Month={svc_month_year}")

            consumers = self.page.evaluate('''() => {
                const results = [];
                const rows = document.querySelectorAll('tr');

                for (const row of rows) {
                    const cells = row.querySelectorAll('td');
                    if (cells.length < 6) continue;  // Skip header/insufficient rows

                    // Invoice view columns: Line#, Consumer, UCI#, SVC Code, SVC Subcode, Auth#, ...
                    const lineNum = cells[0]?.innerText?.trim() || '';
                    const consumerName = cells[1]?.innerText?.trim() || '';
                    const uci = cells[2]?.innerText?.trim() || '';
                    const svcCode = cells[3]?.innerText?.trim() || '';
                    const svcSubcode = cells[4]?.innerText?.trim() || '';
                    const authNumber = cells[5]?.innerText?.trim() || '';

                    // Skip header row (Line# would be non-numeric)
                    if (lineNum && /^\\d+$/.test(lineNum)) {
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

    def open_invoice_details(self, consumer_name: str, service_month_year: str = None, uci: str = None, svc_code: str = None) -> bool:
        """
        Click EDIT to open invoice details, matching by UCI + Service Code + Service M/Y.
        Falls back to multi-consumer invoice matching if direct match not found.
        """
        try:
            logger.info(f"Opening invoice: UCI={uci}, SVC={svc_code}, Month={service_month_year}")
            self._screenshot("09_before_edit_click")

            clicked = False

            # Method 1: Find row matching UCI + Service Code + Service M/Y (single-consumer invoice)
            if uci and service_month_year:
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
                        if (cells.length < 5) continue;

                        // Table: Invoice#, Service Code, Service M/Y, UCI#, Consumer Name, ...
                        const rowSvcCode = cells[1]?.innerText?.trim() || '';
                        const rowMonth = normalizeMonth(cells[2]?.innerText?.trim() || '');
                        const rowUci = cells[3]?.innerText?.trim() || '';

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
                        if (cells.length < 5) continue;

                        const rowSvcCode = cells[1]?.innerText?.trim() || '';
                        const rowMonth = normalizeMonth(cells[2]?.innerText?.trim() || '');
                        const rowUci = cells[3]?.innerText?.trim() || '';

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

    def enter_calendar_units(self, service_days: List[int], units_per_day: int = 1) -> int:
        """Enter units in calendar input fields for specified days"""
        try:
            logger.info(f"Entering {units_per_day} unit(s) for days: {service_days}")

            days_entered = 0

            # Get all input fields in the calendar
            # The calendar structure has day numbers with input fields below them
            for day in service_days:
                try:
                    # Find and fill the input for this day
                    # Calendar inputs are in table cells, day number is visible text
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
                                    input.value = '{units_per_day}';
                                    input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                    input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                                    return true;
                                }}
                            }}
                        }}
                        return false;
                    }}''')

                    if result:
                        days_entered += 1
                        logger.info(f"  Entered unit for day {day}")
                    else:
                        logger.warning(f"  Could not find input for day {day}")

                except Exception as e:
                    logger.warning(f"  Error entering day {day}: {e}")

            time.sleep(1)
            return days_entered

        except Exception as e:
            logger.error(f"Calendar entry failed: {e}")
            return 0

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
                    error_message="Could not open calendar",
                    invoice_units=invoice_units,
                    invoice_amount=invoice_amount
                )

            # Enter service days
            days_entered = self.enter_calendar_units(service_days)

            # Capture billing data from calendar's Invoice Line Summary (before Update)
            portal_data = self.capture_portal_billing_data(uci)

            # Click Update
            if not self.click_update():
                return SubmissionResult(
                    success=False,
                    consumer_name=consumer_name,
                    uci=uci,
                    days_entered=days_entered,
                    error_message="Could not click Update",
                    invoice_units=invoice_units,
                    invoice_amount=invoice_amount
                )

            logger.info(f"✓ Submitted: {consumer_name} ({days_entered} days)")
            return SubmissionResult(
                success=True,
                consumer_name=consumer_name,
                uci=uci,
                days_entered=days_entered,
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
                    error_message="Could not open calendar (invoice already open)",
                    invoice_units=invoice_units,
                    invoice_amount=invoice_amount
                )

            # Enter service days
            days_entered = self.enter_calendar_units(service_days)

            # Capture billing data
            portal_data = self.capture_portal_billing_data(uci)

            # Click Update - returns to invoice view
            if not self.click_update():
                return SubmissionResult(
                    success=False,
                    consumer_name=consumer_name,
                    uci=uci,
                    days_entered=days_entered,
                    error_message="Could not click Update",
                    invoice_units=invoice_units,
                    invoice_amount=invoice_amount
                )

            logger.info(f"Submitted (in-invoice): {consumer_name} ({days_entered} days)")
            return SubmissionResult(
                success=True,
                consumer_name=consumer_name,
                uci=uci,
                days_entered=days_entered,
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
        from collections import defaultdict

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
        Submit all billing records with optimized navigation.
        Groups records by invoice and processes all records in an invoice before moving to the next.

        Args:
            records: List of billing record dictionaries
            provider_name: Name of the service provider (if None, uses spn_id from first record)

        Returns:
            List of SubmissionResult objects
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

        # Cache search results (Level 1)
        self._invoice_search_cache = self.cache_invoice_search_results()

        # Group records by invoice key for efficient batch processing
        grouped_records = self._group_records_by_invoice(records)

        # Helper to normalize month format for comparison
        def normalize_month(m):
            if not m:
                return ''
            parts = m.split('/')
            if len(parts) == 2:
                return parts[0].zfill(2) + '/' + parts[1]
            return m

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
                        # Normalize month format for comparison (8/2025 vs 08/2025)
                        normalized_month = normalize_month(service_month)
                        search_match = next(
                            (inv for inv in self._invoice_search_cache
                             if inv.get('svc_code') == svc_code and normalize_month(inv.get('svc_month', '')) == normalized_month),
                            None
                        )
                        if search_match and not search_match.get('has_uci', True):
                            # This is a multi-consumer invoice - cache contents for efficiency
                            self.cache_multi_consumer_invoice_contents(svc_code, service_month)
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
    with DDSeBillingBot(username, password, headless=False,
                        regional_center=regional_center, portal_url=portal_url) as bot:
        return bot.submit_all_records(records, provider_name)
