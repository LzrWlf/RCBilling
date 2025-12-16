"""
CSV Parser for Office Ally CMS-1500 format
Extracts billing data for Regional Center eBilling submission
"""
import pandas as pd
from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime


@dataclass
class ServiceLine:
    """A single service line item"""
    date_of_service: str
    cpt_code: str
    units: int
    charges: float
    place_of_service: str
    provider_npi: str
    provider_name: str


@dataclass
class Claim:
    """A claim with patient info and service lines"""
    patient_id: str
    patient_first: str
    patient_last: str
    patient_dob: str
    facility_name: str
    service_lines: List[ServiceLine]

    @property
    def patient_name(self) -> str:
        return f"{self.patient_first} {self.patient_last}"

    @property
    def total_charges(self) -> float:
        return sum(line.charges for line in self.service_lines)


def parse_office_ally_csv(filepath: str) -> List[Claim]:
    """
    Parse Office Ally CMS-1500 CSV export format.

    The CSV has up to 6 service lines per row (CPT1-6, etc.)
    """
    df = pd.read_csv(filepath, dtype=str)
    df = df.fillna('')

    claims = []

    for _, row in df.iterrows():
        service_lines = []

        # Extract up to 6 service lines per row
        for i in range(1, 7):
            cpt_col = f'CPT{i}'
            if cpt_col in row and row[cpt_col].strip():
                cpt_code = row[cpt_col].strip()

                # Get corresponding fields for this line
                dos_from = row.get(f'FromDateOfService{i}', '').strip()
                units = row.get(f'Units{i}', '1').strip()
                charges = row.get(f'Charges{i}', '0').strip()
                pos = row.get(f'PlaceOfService{i}', '').strip()
                provider_npi = row.get(f'RenderingPhysNPI{i}', '').strip()

                # Get provider name from main fields (same for all lines)
                provider_first = row.get('PhysicianFirst', '').strip()
                provider_last = row.get('PhysicianLast', '').strip()
                provider_name = f"{provider_first} {provider_last}".strip()

                try:
                    units_int = int(float(units)) if units else 1
                except ValueError:
                    units_int = 1

                try:
                    charges_float = float(charges) if charges else 0.0
                except ValueError:
                    charges_float = 0.0

                service_lines.append(ServiceLine(
                    date_of_service=dos_from,
                    cpt_code=cpt_code,
                    units=units_int,
                    charges=charges_float,
                    place_of_service=pos,
                    provider_npi=provider_npi,
                    provider_name=provider_name
                ))

        if service_lines:
            claim = Claim(
                patient_id=row.get('PatientID', '').strip(),
                patient_first=row.get('PatientFirst', '').strip(),
                patient_last=row.get('PatientLast', '').strip(),
                patient_dob=row.get('PatientDOB', '').strip(),
                facility_name=row.get('FacilityName', '').strip(),
                service_lines=service_lines
            )
            claims.append(claim)

    return claims


def claims_to_dict(claims: List[Claim]) -> List[dict]:
    """Convert claims to dictionary format for JSON/template rendering"""
    result = []
    for claim in claims:
        claim_dict = {
            'patient_id': claim.patient_id,
            'patient_name': claim.patient_name,
            'patient_first': claim.patient_first,
            'patient_last': claim.patient_last,
            'patient_dob': claim.patient_dob,
            'facility_name': claim.facility_name,
            'total_charges': claim.total_charges,
            'service_lines': [
                {
                    'date_of_service': line.date_of_service,
                    'cpt_code': line.cpt_code,
                    'units': line.units,
                    'charges': line.charges,
                    'place_of_service': line.place_of_service,
                    'provider_npi': line.provider_npi,
                    'provider_name': line.provider_name
                }
                for line in claim.service_lines
            ]
        }
        result.append(claim_dict)
    return result
