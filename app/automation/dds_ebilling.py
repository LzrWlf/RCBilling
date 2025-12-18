"""
DDS eBilling Portal Automation using Playwright

This module handles automated login and invoice submission to the
California DDS (Department of Developmental Services) eBilling portal.
Used by all Regional Centers statewide.

Portal URLs by Regional Center (different ports):
- SGPRC (San Gabriel/Pomona): https://ebilling.dds.ca.gov:8379/login
- ELARC (Eastern LA): https://ebilling.dds.ca.gov:8373/login
"""
from playwright.sync_api import sync_playwright, Page, Browser
from dataclasses import dataclass
from typing import List, Optional
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
    """Result of a claim submission"""
    success: bool
    confirmation_number: Optional[str] = None
    error_message: Optional[str] = None
    screenshot_path: Optional[str] = None


class DDSeBillingBot:
    """
    Automation bot for DDS eBilling portal.

    Portal Flow (mapped from SGPRC):
    1. LAUNCH APPLICATION button → opens popup
    2. Login (USERNAME, PASSWORD fields)
    3. ACCEPT user agreement popup
    4. Select provider (click table row)
    5. OK confirmation popup (sometimes auto-dismisses)
    6. Click INVOICES tab
    7. Enter UCI# in search field → click SEARCH
    8. Click invoice row → monthly invoice list
    9. Click "0" in days attend column → calendar view
    10. Click day cell → type units
    11. Click UPDATE to save
    """

    PORTAL_URL = "https://ebilling.dds.ca.gov:8379/login"  # Default to SGPRC

    def __init__(self, username: str, password: str, headless: bool = True,
                 regional_center: str = 'SGPRC', use_chrome: bool = True):
        self.username = username
        self.password = password
        self.headless = headless
        self.use_chrome = use_chrome
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self.playwright = None

        # Set portal URL based on regional center
        self.PORTAL_URL = RC_PORTAL_URLS.get(regional_center, RC_PORTAL_URLS['SGPRC'])

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def start(self):
        """Start browser session"""
        logger.info("Starting Playwright browser...")
        self.playwright = sync_playwright().start()

        # Use Chrome if available, otherwise fall back to Chromium
        launch_args = {
            'headless': self.headless,
            'args': ['--disable-blink-features=AutomationControlled']
        }
        if self.use_chrome:
            launch_args['channel'] = 'chrome'
            logger.info("Using Chrome browser")

        self.browser = self.playwright.chromium.launch(**launch_args)
        self.page = self.browser.new_page()
        self.page.set_viewport_size({"width": 1400, "height": 900})
        logger.info("Browser started successfully")

    def stop(self):
        """Close browser session"""
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        logger.info("Browser session closed")

    def login(self) -> bool:
        """
        Login to DDS eBilling portal.

        Returns:
            True if login successful, False otherwise
        """
        try:
            logger.info(f"Navigating to {self.PORTAL_URL}")
            self.page.goto(self.PORTAL_URL, wait_until="networkidle")

            # Step 1: Click "LAUNCH APPLICATION" button on landing page
            # This opens a NEW WINDOW for login
            logger.info("Looking for LAUNCH APPLICATION button...")

            # Set up popup handler before clicking
            with self.page.expect_popup() as popup_info:
                launch_btn = self.page.query_selector('button:has-text("LAUNCH APPLICATION"), input:has-text("LAUNCH APPLICATION"), a:has-text("LAUNCH APPLICATION"), [value*="LAUNCH"], :has-text("LAUNCH")')
                if launch_btn:
                    logger.info("Clicking LAUNCH APPLICATION...")
                    launch_btn.click()

            # Switch to the login popup window
            login_page = popup_info.value
            login_page.wait_for_load_state("networkidle")
            logger.info("Login window opened")

            # Wait for login form in popup
            login_page.wait_for_selector('input[name="username"], input[id="username"], input[type="text"]', timeout=10000)

            # Fill credentials
            logger.info("Entering credentials...")
            login_page.fill('input[name="username"], input[id="username"], input[type="text"]', self.username)
            login_page.fill('input[name="password"], input[id="password"], input[type="password"]', self.password)

            # Click login button
            login_page.click('button[type="submit"], input[type="submit"], button:has-text("Login"), input[value="Login"]')

            # Wait for navigation after login
            login_page.wait_for_load_state("networkidle")

            # Check for password update requirement
            if login_page.query_selector(':has-text("password update"), :has-text("change password"), :has-text("password expired")'):
                logger.warning("Password update required - manual intervention needed")
                return False

            # Check if login was successful (look for dashboard elements or error messages)
            if login_page.query_selector('.error, .alert-danger, .login-error, :has-text("invalid")'):
                logger.error("Login failed - invalid credentials")
                return False

            # Handle user agreement popup
            logger.info("Checking for user agreement popup...")
            accept_btn = login_page.query_selector('button:has-text("Accept"), button:has-text("I Agree"), button:has-text("ACCEPT"), input[value*="Accept"], input[value*="Agree"], a:has-text("Accept")')
            if accept_btn:
                logger.info("Clicking Accept on user agreement...")
                accept_btn.click()
                login_page.wait_for_load_state("networkidle")

            # Update self.page to point to the logged-in window
            self.page = login_page
            logger.info("Login successful")
            return True

    def select_provider(self, provider_name: str) -> bool:
        """
        Select service provider from the provider selection list.

        Args:
            provider_name: Name of the provider to select (or partial match)

        Returns:
            True if provider selected successfully
        """
        try:
            logger.info(f"Looking for provider: {provider_name}")

            # Look for provider in selection list/portal
            # Try various selector patterns
            provider_selectors = [
                f'text="{provider_name}"',
                f'a:has-text("{provider_name}")',
                f'td:has-text("{provider_name}")',
                f'div:has-text("{provider_name}")',
                f'span:has-text("{provider_name}")',
                f'*:has-text("{provider_name}")'
            ]

            for selector in provider_selectors:
                provider_elem = self.page.query_selector(selector)
                if provider_elem:
                    logger.info(f"Found provider, clicking...")
                    provider_elem.click()
                    break
            else:
                logger.error(f"Provider '{provider_name}' not found")
                return False

            # Wait for and handle "provider is selected" confirmation popup
            self.page.wait_for_timeout(1000)  # Brief wait for popup

            ok_btn = self.page.query_selector('button:has-text("OK"), button:has-text("Ok"), input[value="OK"], input[value="Ok"], button:has-text("ok")')
            if ok_btn:
                logger.info("Clicking OK on provider selection confirmation...")
                ok_btn.click()
                self.page.wait_for_load_state("networkidle")

            logger.info(f"Provider '{provider_name}' selected successfully")
            return True

        except Exception as e:
            logger.error(f"Provider selection error: {str(e)}")
            return False

    def navigate_to_invoice_entry(self) -> bool:
        """
        Navigate to the invoice/claim entry section.
        Clicks on "Invoices" tab at the top of the page.

        Returns:
            True if navigation successful
        """
        try:
            logger.info("Navigating to Invoices tab...")

            # Click on Invoices tab at top of page
            invoice_tab_selectors = [
                'a:has-text("Invoices")',
                'tab:has-text("Invoices")',
                'li:has-text("Invoices")',
                'span:has-text("Invoices")',
                '*[role="tab"]:has-text("Invoices")',
                'a:has-text("INVOICES")',
                'text="Invoices"'
            ]

            for selector in invoice_tab_selectors:
                tab = self.page.query_selector(selector)
                if tab:
                    logger.info(f"Found Invoices tab, clicking...")
                    tab.click()
                    self.page.wait_for_load_state("networkidle")
                    logger.info("Navigated to Invoices")
                    return True

            logger.error("Could not find Invoices tab")
            return False

        except Exception as e:
            logger.error(f"Navigation error: {str(e)}")
            return False

    def search_by_uci(self, uci_number: str) -> bool:
        """
        Search for invoices by UCI# (unique client identifier).
        UCI# in portal = PatientID in Office Ally CSV.

        Args:
            uci_number: The UCI# to search for

        Returns:
            True if search successful
        """
        try:
            logger.info(f"Searching for UCI#: {uci_number}")

            # Find UCI# input field - try various selectors
            uci_input_selectors = [
                'input[name*="uci" i]',
                'input[id*="uci" i]',
                'input[placeholder*="UCI" i]',
                'input[name*="UCI"]',
                'input[id*="UCI"]',
            ]

            uci_input = None
            for selector in uci_input_selectors:
                uci_input = self.page.query_selector(selector)
                if uci_input:
                    break

            if not uci_input:
                # Try to find by label
                label = self.page.query_selector('label:has-text("UCI")')
                if label:
                    input_id = label.get_attribute('for')
                    if input_id:
                        uci_input = self.page.query_selector(f'#{input_id}')

            if uci_input:
                logger.info("Found UCI# input field, entering number...")
                uci_input.fill(uci_number)
            else:
                logger.warning("Could not find UCI# input field, trying blank search")

            # Click Search button
            return self.search_invoices()

        except Exception as e:
            logger.error(f"UCI search error: {str(e)}")
            return False

    def search_invoices(self) -> bool:
        """
        Search for invoices. Click Search to show results.

        Returns:
            True if search successful
        """
        try:
            logger.info("Clicking Search button...")

            search_btn_selectors = [
                'input[value="Search"]',
                'button:has-text("Search")',
                'input[type="submit"][value*="Search"]',
                'button:has-text("SEARCH")',
                'a:has-text("Search")',
                '#searchBtn',
                '.search-button'
            ]

            for selector in search_btn_selectors:
                search_btn = self.page.query_selector(selector)
                if search_btn:
                    search_btn.click()
                    self.page.wait_for_load_state("networkidle")
                    self.page.wait_for_timeout(1000)  # Wait for results to load
                    logger.info("Invoice search completed")
                    return True

            logger.error("Could not find Search button")
            return False

        except Exception as e:
            logger.error(f"Invoice search error: {str(e)}")
            return False

    def click_invoice_row(self, invoice_identifier: str = None) -> bool:
        """
        Click on an invoice row from the search results.
        This opens the monthly invoice list for that client.

        Args:
            invoice_identifier: Optional text to identify specific invoice (client name, UCI#, etc.)

        Returns:
            True if successfully clicked on an invoice row
        """
        try:
            logger.info("Looking for invoice row to click...")

            if invoice_identifier:
                # Try to find row containing the identifier and click it
                row = self.page.query_selector(f'tr:has-text("{invoice_identifier}")')
                if row:
                    row.click()
                    self.page.wait_for_load_state("networkidle")
                    self.page.wait_for_timeout(1000)
                    logger.info(f"Clicked invoice row: {invoice_identifier}")
                    return True

            # Otherwise click first available data row (skip header)
            rows = self.page.query_selector_all('table tr')
            for row in rows[1:]:  # Skip header row
                # Check if it's a data row (has td elements)
                if row.query_selector('td'):
                    row.click()
                    self.page.wait_for_load_state("networkidle")
                    self.page.wait_for_timeout(1000)
                    logger.info("Clicked first invoice row")
                    return True

            logger.error("Could not find any invoice rows")
            return False

        except Exception as e:
            logger.error(f"Invoice row click error: {str(e)}")
            return False

    def click_days_attend(self, client_identifier: str = None) -> bool:
        """
        Click on the "0" (or number) in the days attend column to open the calendar.
        This is the entry point to the calendar view for entering service dates.

        Args:
            client_identifier: Optional client name or UCI# to find specific row

        Returns:
            True if successfully opened calendar view
        """
        try:
            logger.info("Looking for days attend link to open calendar...")

            if client_identifier:
                # Find row with client identifier
                row = self.page.query_selector(f'tr:has-text("{client_identifier}")')
                if row:
                    # Look for clickable element (usually "0" or a number) in days column
                    days_link = row.query_selector('a, td a, [onclick]')
                    if days_link:
                        days_link.click()
                        self.page.wait_for_load_state("networkidle")
                        self.page.wait_for_timeout(1000)
                        logger.info(f"Opened calendar for: {client_identifier}")
                        return True

            # Try clicking on any "0" link or the first clickable cell
            zero_selectors = [
                'a:has-text("0")',
                'td a:has-text("0")',
                'a[href*="calendar"]',
                'td:nth-child(2) a',  # Days attend is often second column
            ]

            for selector in zero_selectors:
                link = self.page.query_selector(selector)
                if link:
                    link.click()
                    self.page.wait_for_load_state("networkidle")
                    self.page.wait_for_timeout(1000)
                    logger.info("Opened calendar view")
                    return True

            logger.error("Could not find days attend link")
            return False

        except Exception as e:
            logger.error(f"Days attend click error: {str(e)}")
            return False

    def click_sessions_input(self, client_name: str = None) -> bool:
        """
        Click on the sessions/number input link for a client in the invoice.
        This opens the calendar view for that client.

        Args:
            client_name: Optional client name to find specific row

        Returns:
            True if successfully clicked into calendar view
        """
        try:
            logger.info("Looking for sessions input link...")

            if client_name:
                # Find row with client name
                row = self.page.query_selector(f'tr:has-text("{client_name}")')
                if row:
                    # Click on sessions/number link in that row
                    sessions_link = row.query_selector('a, input[type="text"], td:nth-child(2) a')
                    if sessions_link:
                        sessions_link.click()
                        self.page.wait_for_load_state("networkidle")
                        logger.info(f"Opened calendar for: {client_name}")
                        return True

            # Try generic session input selectors
            session_selectors = [
                'a:has-text("session")',
                'a:has-text("Session")',
                'td a',  # Links in table cells
                '.session-link'
            ]

            for selector in session_selectors:
                link = self.page.query_selector(selector)
                if link:
                    link.click()
                    self.page.wait_for_load_state("networkidle")
                    logger.info("Opened calendar view")
                    return True

            logger.error("Could not find sessions input link")
            return False

        except Exception as e:
            logger.error(f"Sessions link error: {str(e)}")
            return False

    def enter_calendar_dates(self, service_dates: List[str], units: int = 1) -> bool:
        """
        Enter service units into calendar for specified dates.
        Calendar shows eligible days - we input '1' (or units) into each service date.

        Args:
            service_dates: List of dates in MM/DD/YYYY format
            units: Number to enter for each date (usually 1)

        Returns:
            True if dates entered successfully
        """
        try:
            logger.info(f"Entering {len(service_dates)} service dates into calendar...")

            for date_str in service_dates:
                # Parse the date
                parts = date_str.split('/')
                if len(parts) == 3:
                    month, day, year = parts
                    day = str(int(day))  # Remove leading zeros for matching

                    # Find the calendar cell for this date
                    # Calendar cells typically have the day number or date as text/attribute
                    date_selectors = [
                        f'td:has-text("{day}") input',
                        f'input[data-date="{date_str}"]',
                        f'td[data-day="{day}"] input',
                        f'//td[contains(text(),"{day}")]//input',
                    ]

                    for selector in date_selectors:
                        try:
                            if selector.startswith('//'):
                                cell_input = self.page.query_selector(f'xpath={selector}')
                            else:
                                cell_input = self.page.query_selector(selector)

                            if cell_input:
                                cell_input.fill(str(units))
                                logger.info(f"Entered {units} for date: {date_str}")
                                break
                        except:
                            continue

            return True

        except Exception as e:
            logger.error(f"Calendar entry error: {str(e)}")
            return False

    def submit_calendar(self, go_to_next: bool = False) -> bool:
        """
        Submit the calendar entries.

        Button options on calendar view:
        - Update: Save and return to invoice list
        - Update-next: Save and go to next client's calendar
        - Close: Cancel without saving

        Args:
            go_to_next: If True, click Update-next. If False, click Update.

        Returns:
            True if submitted successfully
        """
        try:
            if go_to_next:
                logger.info("Clicking Update-next to continue to next calendar...")
                btn_selectors = [
                    'input[value="Update-next"]',
                    'button:has-text("Update-next")',
                    'input[value*="next" i]',
                    'button:has-text("Next")',
                ]
            else:
                logger.info("Clicking Update to save and return...")
                btn_selectors = [
                    'input[value="Update"]',
                    'button:has-text("Update")',
                    'input[type="submit"][value="Update"]',
                ]

            for selector in btn_selectors:
                btn = self.page.query_selector(selector)
                if btn:
                    btn.click()
                    self.page.wait_for_load_state("networkidle")
                    self.page.wait_for_timeout(1000)
                    logger.info("Calendar updated successfully")
                    return True

            logger.error("Could not find Update button")
            return False

        except Exception as e:
            logger.error(f"Calendar submit error: {str(e)}")
            return False

    def close_calendar(self) -> bool:
        """
        Close calendar without saving (click Close button).

        Returns:
            True if closed successfully
        """
        try:
            logger.info("Clicking Close to cancel...")
            close_selectors = [
                'input[value="Close"]',
                'button:has-text("Close")',
                'a:has-text("Close")',
            ]

            for selector in close_selectors:
                btn = self.page.query_selector(selector)
                if btn:
                    btn.click()
                    self.page.wait_for_load_state("networkidle")
                    logger.info("Calendar closed")
                    return True

            logger.error("Could not find Close button")
            return False

        except Exception as e:
            logger.error(f"Calendar close error: {str(e)}")
            return False

    def enter_service_line(
        self,
        client_name: str,
        date_of_service: str,
        cpt_code: str,
        units: int,
        charges: float,
        provider_name: str
    ) -> SubmissionResult:
        """
        Enter a single service line into the calendar interface.
        NOTE: This is a legacy method - use the new flow methods instead:
        click_invoice_edit() -> click_sessions_input() -> enter_calendar_dates() -> submit_calendar()

        Args:
            client_name: Patient/client full name
            date_of_service: Date in MM/DD/YYYY format
            cpt_code: CPT/service code
            units: Number of units
            charges: Dollar amount
            provider_name: Rendering provider name

        Returns:
            SubmissionResult with success status and any confirmation
        """
        try:
            logger.info(f"Entering service: {client_name} - {date_of_service} - {cpt_code}")

            # Use the new calendar-based flow
            if not self.click_sessions_input(client_name):
                return SubmissionResult(success=False, error_message="Could not open calendar")

            if not self.enter_calendar_dates([date_of_service], units):
                return SubmissionResult(success=False, error_message="Could not enter date")

            if not self.submit_calendar():
                return SubmissionResult(success=False, error_message="Could not submit calendar")

            return SubmissionResult(
                success=True,
                confirmation_number="SUBMITTED",
                error_message=None
            )

        except Exception as e:
            logger.error(f"Service entry error: {str(e)}")
            return SubmissionResult(
                success=False,
                error_message=str(e)
            )

    def submit_invoice(self, claims: List[dict]) -> List[SubmissionResult]:
        """
        Submit all claims to the eBilling portal.

        Args:
            claims: List of claim dictionaries from CSV parser

        Returns:
            List of SubmissionResult for each claim
        """
        results = []

        if not self.login():
            return [SubmissionResult(
                success=False,
                error_message="Login failed"
            )]

        if not self.navigate_to_invoice_entry():
            return [SubmissionResult(
                success=False,
                error_message="Navigation failed"
            )]

        for claim in claims:
            for line in claim.get('service_lines', []):
                result = self.enter_service_line(
                    client_name=claim.get('patient_name', ''),
                    date_of_service=line.get('date_of_service', ''),
                    cpt_code=line.get('cpt_code', ''),
                    units=line.get('units', 1),
                    charges=line.get('charges', 0),
                    provider_name=line.get('provider_name', '')
                )
                results.append(result)

                # Small delay between entries to avoid rate limiting
                time.sleep(0.5)

        return results

    def take_screenshot(self, name: str = "screenshot") -> str:
        """Capture screenshot for debugging/confirmation"""
        path = f"screenshots/{name}_{int(time.time())}.png"
        self.page.screenshot(path=path)
        logger.info(f"Screenshot saved: {path}")
        return path

    def test_connection(self) -> bool:
        """Test if portal is reachable and credentials work"""
        try:
            self.start()
            result = self.login()
            return result
        except Exception as e:
            logger.error(f"Connection test failed: {str(e)}")
            return False
        finally:
            self.stop()


# Convenience function for quick submission
def submit_to_ebilling(
    username: str,
    password: str,
    claims: List[dict],
    headless: bool = True
) -> List[SubmissionResult]:
    """
    One-shot function to submit claims to DDS eBilling.

    Args:
        username: eBilling portal username
        password: eBilling portal password
        claims: List of claim dictionaries
        headless: Run browser in headless mode

    Returns:
        List of SubmissionResult for each line item
    """
    with DDSeBillingBot(username, password, headless) as bot:
        return bot.submit_invoice(claims)
