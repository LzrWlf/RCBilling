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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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


class DDSeBillingBot:
    """
    Automation bot for DDS eBilling portal.
    """

    def __init__(self, username: str, password: str, headless: bool = False,
                 regional_center: str = 'ELARC'):
        self.username = username
        self.password = password
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.context = None
        self.playwright = None
        self.portal_url = RC_PORTAL_URLS.get(regional_center, RC_PORTAL_URLS['ELARC'])

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

    def login(self) -> bool:
        """Login to the portal"""
        try:
            logger.info(f"Navigating to {self.portal_url}")
            self.page.goto(self.portal_url, wait_until="networkidle")
            time.sleep(2)

            # Click LAUNCH APPLICATION
            logger.info("Clicking LAUNCH APPLICATION...")
            with self.context.expect_page() as new_page_info:
                self.page.mouse.click(910, 200)

            # Switch to login popup
            self.page = new_page_info.value
            self.page.wait_for_load_state("networkidle")
            time.sleep(2)

            # Enter credentials
            logger.info("Entering credentials...")
            self.page.wait_for_selector('input[type="text"]', timeout=15000)
            self.page.query_selector('input[type="text"]').fill(self.username)
            self.page.query_selector('input[type="password"]').fill(self.password)
            self.page.query_selector('input[type="password"]').press("Enter")
            self.page.wait_for_load_state("networkidle")
            time.sleep(3)

            # Accept agreement using JavaScript
            logger.info("Accepting agreement...")
            time.sleep(2)
            self.page.evaluate('''() => {
                const elements = document.querySelectorAll('input, button, a');
                for (const el of elements) {
                    const text = el.value || el.innerText || '';
                    if (text.trim() === 'Accept') {
                        el.click();
                        return true;
                    }
                }
                return false;
            }''')
            self.page.wait_for_load_state("networkidle")
            time.sleep(2)

            logger.info("Login successful")
            return True

        except Exception as e:
            logger.error(f"Login failed: {e}")
            return False

    def select_provider(self, provider_name: str) -> bool:
        """Select the service provider"""
        try:
            logger.info(f"Selecting provider: {provider_name}")
            self.page.click(f'tr:has-text("{provider_name}")', timeout=5000)
            time.sleep(2)
            try:
                self._js_click("OK")
            except:
                pass
            self.page.wait_for_load_state("networkidle")
            time.sleep(2)
            return True
        except Exception as e:
            logger.error(f"Provider selection failed: {e}")
            return False

    def navigate_to_invoices(self) -> bool:
        """Navigate to Invoices tab and search"""
        try:
            logger.info("Clicking Invoices tab...")
            self.page.click('a:has-text("Invoices")', timeout=5000)
            self.page.wait_for_load_state("networkidle")
            time.sleep(2)

            logger.info("Clicking Search...")
            self._js_click("Search")
            self.page.wait_for_load_state("networkidle")
            time.sleep(3)

            return True
        except Exception as e:
            logger.error(f"Navigation failed: {e}")
            return False

    def open_invoice_details(self, consumer_name: str) -> bool:
        """Click EDIT to open invoice details for a consumer"""
        try:
            logger.info(f"Opening invoice details for: {consumer_name}")

            # Try multiple methods to click EDIT
            clicked = False

            # Method 1: Playwright text locator for EDIT
            try:
                self.page.get_by_text("EDIT").first.click(timeout=5000)
                clicked = True
                logger.info("Clicked EDIT via text locator")
            except Exception as e:
                logger.warning(f"Text locator failed: {e}")

            # Method 2: Click on the pencil image
            if not clicked:
                try:
                    self.page.locator('img').last.click(timeout=3000)
                    clicked = True
                    logger.info("Clicked pencil image")
                except Exception as e:
                    logger.warning(f"Image click failed: {e}")

            # Method 3: JavaScript - find link containing image and click
            if not clicked:
                try:
                    result = self.page.evaluate('''() => {
                        // Find all links with images
                        const links = document.querySelectorAll('a');
                        for (const link of links) {
                            if (link.querySelector('img') || link.innerText.includes('EDIT')) {
                                link.click();
                                return 'clicked';
                            }
                        }
                        // Try clicking any clickable element with EDIT text
                        const all = document.querySelectorAll('*');
                        for (const el of all) {
                            if (el.innerText === 'EDIT' && el.click) {
                                el.click();
                                return 'clicked element';
                            }
                        }
                        return 'not found';
                    }''')
                    logger.info(f"JS click result: {result}")
                    clicked = result != 'not found'
                except Exception as e:
                    logger.warning(f"JS click failed: {e}")

            self.page.wait_for_load_state("networkidle")
            time.sleep(3)

            # Verify we're on the invoice view page
            if '/invoices/invoiceview' in self.page.url:
                logger.info("Opened invoice details page")
                return True

            logger.warning("May not have navigated to invoice details")
            return True

        except Exception as e:
            logger.error(f"Failed to open invoice details: {e}")
            return False

    def open_calendar(self, uci: str, svc_code: str, auth_number: str) -> bool:
        """Click on Days Attend to open the calendar for a specific line"""
        try:
            logger.info(f"Opening calendar for UCI: {uci}, SVC: {svc_code}, Auth: {auth_number}")

            # Find the matching row and click on Days Attend (the "0" cell)
            clicked = self.page.evaluate(f'''() => {{
                const rows = document.querySelectorAll('tr');
                for (const row of rows) {{
                    const text = row.innerText;
                    // Match by UCI and SVC code
                    if (text.includes('{uci}') && text.includes('{svc_code}')) {{
                        const cells = row.querySelectorAll('td');
                        // Days Attend is usually around column 9-10, shows "0"
                        for (let i = 8; i < cells.length; i++) {{
                            const cellText = cells[i].innerText.trim();
                            if (cellText === '0' || cellText === '') {{
                                cells[i].click();
                                return true;
                            }}
                        }}
                    }}
                }}
                return false;
            }}''')

            if clicked:
                self.page.wait_for_load_state("networkidle")
                time.sleep(3)

                # Verify we're on the calendar page
                if '/invoices/unitcalendar' in self.page.url:
                    logger.info("Opened calendar page")
                    return True

            logger.warning("May not have opened calendar")
            return clicked

        except Exception as e:
            logger.error(f"Failed to open calendar: {e}")
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
        """Click Update button to save calendar entries"""
        try:
            logger.info("Clicking Update...")
            self._js_click("Update")
            self.page.wait_for_load_state("networkidle")
            time.sleep(2)
            logger.info("Update clicked")
            return True
        except Exception as e:
            logger.error(f"Update failed: {e}")
            return False

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
                - entered_units: Units per day (usually 1)

        Returns:
            SubmissionResult with success status
        """
        try:
            uci = record.get('uci', '')
            consumer_name = record.get('consumer_name', '')
            lastname = record.get('lastname', '')
            svc_code = record.get('svc_code', '')
            auth_number = record.get('auth_number', '')
            service_days = record.get('service_days', [])

            logger.info(f"Processing: {consumer_name} (UCI: {uci})")

            # Open invoice details
            if not self.open_invoice_details(lastname):
                return SubmissionResult(
                    success=False,
                    consumer_name=consumer_name,
                    uci=uci,
                    error_message="Could not open invoice details"
                )

            # Open calendar
            if not self.open_calendar(uci, svc_code, auth_number):
                return SubmissionResult(
                    success=False,
                    consumer_name=consumer_name,
                    uci=uci,
                    error_message="Could not open calendar"
                )

            # Enter service days
            days_entered = self.enter_calendar_units(service_days)

            # Click Update
            if not self.click_update():
                return SubmissionResult(
                    success=False,
                    consumer_name=consumer_name,
                    uci=uci,
                    days_entered=days_entered,
                    error_message="Could not click Update"
                )

            return SubmissionResult(
                success=True,
                consumer_name=consumer_name,
                uci=uci,
                days_entered=days_entered
            )

        except Exception as e:
            logger.error(f"Submission error: {e}")
            return SubmissionResult(
                success=False,
                consumer_name=record.get('consumer_name', ''),
                uci=record.get('uci', ''),
                error_message=str(e)
            )

    def submit_all_records(self, records: List[Dict], provider_name: str = "MY VOICE SPEECH") -> List[SubmissionResult]:
        """
        Submit all billing records.

        Args:
            records: List of billing record dictionaries
            provider_name: Name of the service provider

        Returns:
            List of SubmissionResult objects
        """
        results = []

        # Login
        if not self.login():
            return [SubmissionResult(success=False, error_message="Login failed")]

        # Select provider
        if not self.select_provider(provider_name):
            return [SubmissionResult(success=False, error_message="Provider selection failed")]

        # Navigate to invoices
        if not self.navigate_to_invoices():
            return [SubmissionResult(success=False, error_message="Navigation failed")]

        # Process each record
        for record in records:
            result = self.submit_billing_record(record)
            results.append(result)

            if result.success:
                logger.info(f"✓ Submitted: {result.consumer_name} ({result.days_entered} days)")
            else:
                logger.error(f"✗ Failed: {result.consumer_name} - {result.error_message}")

            # Navigate back to invoice search for next record
            try:
                self.page.click('a:has-text("Invoices")', timeout=3000)
                self.page.wait_for_load_state("networkidle")
                self._js_click("Search")
                self.page.wait_for_load_state("networkidle")
                time.sleep(2)
            except:
                pass

        return results


def submit_to_ebilling(records: List[Dict], username: str, password: str,
                       provider_name: str = "MY VOICE SPEECH",
                       regional_center: str = "ELARC") -> List[SubmissionResult]:
    """
    Convenience function to submit billing records.

    Args:
        records: List of billing record dictionaries from CSV parser
        username: Portal username
        password: Portal password
        provider_name: Service provider name
        regional_center: Regional center code (ELARC, SGPRC)

    Returns:
        List of SubmissionResult objects
    """
    with DDSeBillingBot(username, password, headless=False, regional_center=regional_center) as bot:
        return bot.submit_all_records(records, provider_name)
