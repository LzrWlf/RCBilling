# RCBilling

Regional Center Invoice Automation - Automates eBilling submission to California DDS portal.

## Quick Start

```bash
cd ~/Desktop/RCBilling
source venv/bin/activate
python run.py
```

Then open http://localhost:5000 in your browser.

## Features

- **CSV Upload**: Parse Office Ally CMS-1500 format exports
- **Preview**: Review claims before submission
- **Automation**: Playwright-based bot logs into DDS eBilling and enters claims
- **Secure Storage**: Encrypted credential storage

## Usage

1. Export billing data from your EMR as CSV (Office Ally format)
2. Upload CSV at http://localhost:5000
3. Review parsed claims in preview
4. Configure eBilling credentials in Settings
5. Click "Submit to eBilling" to automate entry

## Supported

- **Portal**: DDS eBilling (ebilling.dds.ca.gov) - works for all CA Regional Centers
- **Target RC**: ELARC (Eastern Los Angeles Regional Center)
- **CSV Format**: Office Ally CMS-1500 export

## Development

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Run in development
python run.py
```

## Project Structure

```
RCBilling/
├── app/
│   ├── automation/        # Playwright bot for DDS portal
│   ├── templates/         # HTML templates
│   ├── static/           # CSS/JS assets
│   ├── csv_parser.py     # Office Ally CSV parser
│   ├── credential_manager.py  # Encrypted credential storage
│   └── routes.py         # Flask routes
├── config.py             # App configuration
├── run.py               # Entry point
└── requirements.txt     # Python dependencies
```
