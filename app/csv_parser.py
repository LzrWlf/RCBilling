"""
CSV Parser for Regional Center Billing Format
Extracts billing data for DDS eBilling portal submission
"""
import pandas as pd
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime


@dataclass
class BillingRecord:
    """A billing record for a consumer's monthly services"""
    # Identifiers
    uci: str                    # UCI# - Consumer ID
    lastname: str               # Consumer last name
    firstname: str              # Consumer first name
    auth_number: str            # Authorization number
    svc_code: str               # Service code (e.g., 116)
    svc_subcode: str            # Service subcode (e.g., 1FK)
    svc_month_year: str         # Service month/year (e.g., 2025-12-01)

    # Provider info
    spn_id: str                 # Service Provider Number ID

    # Service days (1-31) - which days had service
    service_days: List[int] = field(default_factory=list)

    # Totals
    entered_units: float = 0.0
    entered_amount: float = 0.0

    @property
    def consumer_name(self) -> str:
        """Full consumer name as it appears in portal"""
        return f"{self.lastname.upper()}, {self.firstname.upper()}"

    @property
    def consumer_name_display(self) -> str:
        """Consumer name for display"""
        return f"{self.firstname} {self.lastname}"

    @property
    def service_month(self) -> str:
        """Extract month/year in MM/YYYY format"""
        try:
            # Parse date like "2025-12-01" or " 2025-12-01"
            date_str = self.svc_month_year.strip()
            if '-' in date_str:
                parts = date_str.split('-')
                return f"{parts[1]}/{parts[0]}"  # MM/YYYY
            return date_str
        except:
            return self.svc_month_year

    @property
    def days_count(self) -> int:
        """Number of service days"""
        return len(self.service_days)


def parse_rc_billing_csv(filepath: str) -> List[BillingRecord]:
    """
    Parse Regional Center Billing CSV format.

    Columns:
    - RecType, RCID, AttOnlyFlag, SPNID, UCI, Lastname, Firstname
    - AuthNumber, SVCCode, SVCSCode, SVCMnYr
    - IndustryType, WageAmt, WageType
    - Day1-Day31 (service days)
    - EnteredUnits, EnteredAmount
    """
    df = pd.read_csv(filepath, dtype=str)
    df = df.fillna('')

    records = []

    for _, row in df.iterrows():
        # Skip header rows or non-data rows
        rec_type = str(row.get('RecType', '')).strip().strip('"')
        if rec_type != 'D':
            continue

        # Extract service days (Day1-Day31)
        service_days = []
        for day_num in range(1, 32):
            day_col = f'Day{day_num}'
            if day_col in row:
                day_val = str(row[day_col]).strip().strip('"')
                if day_val and day_val not in ['', '0']:
                    service_days.append(day_num)

        # Parse units and amount
        try:
            units_str = str(row.get('EnteredUnits', '0')).strip().strip('"')
            entered_units = float(units_str) if units_str else 0.0
        except ValueError:
            entered_units = 0.0

        try:
            # Handle the weird format where amount might be attached to units column
            amount_col = 'EnteredAmount' if 'EnteredAmount' in row else 'EnteredAmount"D"'
            amount_str = str(row.get(amount_col, '0')).strip().strip('"')
            entered_amount = float(amount_str) if amount_str else 0.0
        except ValueError:
            entered_amount = 0.0

        # Clean string fields
        def clean(val):
            return str(val).strip().strip('"')

        record = BillingRecord(
            uci=clean(row.get('UCI', '')),
            lastname=clean(row.get('Lastname', '')),
            firstname=clean(row.get('Firstname', '')),
            auth_number=clean(row.get('AuthNumber', '')),
            svc_code=clean(row.get('SVCCode', '')),
            svc_subcode=clean(row.get('SVCSCode', '')),
            svc_month_year=clean(row.get('SVCMnYr', '')),
            spn_id=clean(row.get('SPNID', '')),
            service_days=service_days,
            entered_units=entered_units,
            entered_amount=entered_amount
        )
        records.append(record)

    return records


def records_to_dict(records: List[BillingRecord]) -> List[dict]:
    """Convert billing records to dictionary format for JSON/template rendering"""
    result = []
    for rec in records:
        rec_dict = {
            'uci': rec.uci,
            'consumer_name': rec.consumer_name,
            'consumer_name_display': rec.consumer_name_display,
            'lastname': rec.lastname,
            'firstname': rec.firstname,
            'auth_number': rec.auth_number,
            'svc_code': rec.svc_code,
            'svc_subcode': rec.svc_subcode,
            'svc_month_year': rec.svc_month_year,
            'service_month': rec.service_month,
            'spn_id': rec.spn_id,
            'service_days': rec.service_days,
            'days_count': rec.days_count,
            'entered_units': rec.entered_units,
            'entered_amount': rec.entered_amount,
        }
        result.append(rec_dict)
    return result


# Keep old function names for backwards compatibility
def parse_office_ally_csv(filepath: str) -> List[BillingRecord]:
    """Alias for parse_rc_billing_csv for backwards compatibility"""
    return parse_rc_billing_csv(filepath)


def claims_to_dict(claims: List[BillingRecord]) -> List[dict]:
    """Alias for records_to_dict for backwards compatibility"""
    return records_to_dict(claims)
