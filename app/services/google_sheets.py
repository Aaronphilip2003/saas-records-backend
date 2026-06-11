import calendar
import logging
from datetime import datetime, timezone

from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.core.config import settings
from app.core.supabase import supabase

logger = logging.getLogger(__name__)

INVOICE_HEADERS = [
    "Synced At", "Doc ID", "Invoice #", "Invoice Date", "Due Date",
    "Vendor Name", "Vendor Tax ID", "Bill To", "Currency",
    "Subtotal", "Discount", "Tax Rate %", "Tax Amount",
    "Total Amount", "Payment Terms", "Payment Status", "Notes",
]

SUMMARY_HEADERS = ["Month", "Invoice Count", "Total Invoiced", "Total Paid", "Total Pending", "Total Overdue"]

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _build_summary_rows(year_start: int, year_end: int) -> list[list]:
    rows = []
    for yr in range(year_start, year_end + 1):
        for mon in range(1, 13):
            last = calendar.monthrange(yr, mon)[1]
            label = f"{MONTH_NAMES[mon - 1]} {yr}"
            rows.append([
                label,
                f"=COUNTIFS(Invoices!D:D,\">=\"&DATE({yr},{mon},1),Invoices!D:D,\"<=\"&DATE({yr},{mon},{last}))",
                f"=SUMIFS(Invoices!N:N,Invoices!D:D,\">=\"&DATE({yr},{mon},1),Invoices!D:D,\"<=\"&DATE({yr},{mon},{last}))",
                f"=SUMIFS(Invoices!N:N,Invoices!D:D,\">=\"&DATE({yr},{mon},1),Invoices!D:D,\"<=\"&DATE({yr},{mon},{last}),Invoices!P:P,\"Paid\")",
                f"=SUMIFS(Invoices!N:N,Invoices!D:D,\">=\"&DATE({yr},{mon},1),Invoices!D:D,\"<=\"&DATE({yr},{mon},{last}),Invoices!P:P,\"Pending\")",
                f"=SUMIFS(Invoices!N:N,Invoices!D:D,\">=\"&DATE({yr},{mon},1),Invoices!D:D,\"<=\"&DATE({yr},{mon},{last}),Invoices!P:P,\"Overdue\")",
            ])
    return rows

_NAVY = {"red": 0.102, "green": 0.102, "blue": 0.176}
_WHITE = {"red": 1.0, "green": 1.0, "blue": 1.0}
_LIGHT_GREY = {"red": 0.957, "green": 0.965, "blue": 0.980}


def _get_credentials(user_id: str) -> Credentials:
    result = supabase.table("google_integrations").select("*").eq("user_id", user_id).single().execute()
    if not result.data:
        raise ValueError("Google Sheets not connected")

    row = result.data
    expiry = None
    if row.get("token_expiry"):
        expiry = datetime.fromisoformat(row["token_expiry"].replace("Z", "+00:00"))
        # google-auth uses naive utcnow() internally, so expiry must also be naive
        if expiry.tzinfo is not None:
            expiry = expiry.replace(tzinfo=None)

    creds = Credentials(
        token=row["access_token"],
        refresh_token=row["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        expiry=expiry,
    )

    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
        supabase.table("google_integrations").update({
            "access_token": creds.token,
            "token_expiry": creds.expiry.isoformat() if creds.expiry else None,
        }).eq("user_id", user_id).execute()

    return creds


def create_invoice_spreadsheet(creds: Credentials) -> tuple[str, str]:
    """Creates a fully formatted spreadsheet in the user's Drive. Returns (spreadsheet_id, url)."""
    service = build("sheets", "v4", credentials=creds)

    # 1. Create spreadsheet with two sheets
    spreadsheet = service.spreadsheets().create(body={
        "properties": {"title": "SaaS Records — Invoice Tracker"},
        "sheets": [
            {"properties": {"sheetId": 0, "title": "Invoices", "index": 0}},
            {"properties": {"sheetId": 1, "title": "Monthly Summary", "index": 1}},
        ],
    }).execute()

    ss_id = spreadsheet["spreadsheetId"]
    ss_url = spreadsheet["spreadsheetUrl"]

    # 2. Write headers
    service.spreadsheets().values().batchUpdate(spreadsheetId=ss_id, body={
        "valueInputOption": "USER_ENTERED",
        "data": [
            {"range": "Invoices!A1", "values": [INVOICE_HEADERS]},
            {"range": "Monthly Summary!A1", "values": [SUMMARY_HEADERS]},
        ],
    }).execute()

    # 3. Write monthly summary rows — default to current year -1 through +1
    current_year = datetime.now(timezone.utc).year
    month_rows = _build_summary_rows(current_year - 1, current_year + 1)

    service.spreadsheets().values().update(
        spreadsheetId=ss_id,
        range="Monthly Summary!A2",
        valueInputOption="USER_ENTERED",
        body={"values": month_rows},
    ).execute()


    # 4. Formatting via batchUpdate
    inv_col_widths = [130, 220, 110, 100, 100, 160, 110, 160, 80, 90, 80, 90, 90, 100, 120, 120, 200]
    sum_col_widths = [130, 110, 120, 100, 110, 110]

    requests = [
        # Freeze headers
        {"updateSheetProperties": {"properties": {"sheetId": 0, "gridProperties": {"frozenRowCount": 1}}, "fields": "gridProperties.frozenRowCount"}},
        {"updateSheetProperties": {"properties": {"sheetId": 1, "gridProperties": {"frozenRowCount": 1}}, "fields": "gridProperties.frozenRowCount"}},

        # Invoices header style
        {"repeatCell": {
            "range": {"sheetId": 0, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {
                "backgroundColor": _NAVY,
                "textFormat": {"foregroundColor": _WHITE, "bold": True},
                "verticalAlignment": "MIDDLE",
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat,verticalAlignment)",
        }},

        # Monthly Summary header style
        {"repeatCell": {
            "range": {"sheetId": 1, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {
                "backgroundColor": _NAVY,
                "textFormat": {"foregroundColor": _WHITE, "bold": True},
            }},
            "fields": "userEnteredFormat(backgroundColor,textFormat)",
        }},

        # Date format: Invoice Date (col D=3) and Due Date (col E=4)
        {"repeatCell": {
            "range": {"sheetId": 0, "startRowIndex": 1, "startColumnIndex": 3, "endColumnIndex": 5},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"}}},
            "fields": "userEnteredFormat.numberFormat",
        }},

        # Number format: amount columns J-N (indices 9-13)
        {"repeatCell": {
            "range": {"sheetId": 0, "startRowIndex": 1, "startColumnIndex": 9, "endColumnIndex": 14},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}}},
            "fields": "userEnteredFormat.numberFormat",
        }},

        # Number format: Monthly Summary amounts (cols C-F = indices 2-5)
        {"repeatCell": {
            "range": {"sheetId": 1, "startRowIndex": 1, "startColumnIndex": 2, "endColumnIndex": 6},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}}},
            "fields": "userEnteredFormat.numberFormat",
        }},

        # Payment Status dropdown (col P = index 15)
        {"setDataValidation": {
            "range": {"sheetId": 0, "startRowIndex": 1, "startColumnIndex": 15, "endColumnIndex": 16},
            "rule": {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [
                        {"userEnteredValue": "Pending"},
                        {"userEnteredValue": "Paid"},
                        {"userEnteredValue": "Overdue"},
                        {"userEnteredValue": "Cancelled"},
                    ],
                },
                "showCustomUi": True,
                "strict": True,
            },
        }},

        # Row banding — Invoices
        {"addBanding": {"bandedRange": {
            "bandedRangeId": 1,
            "range": {"sheetId": 0, "startRowIndex": 0, "startColumnIndex": 0, "endColumnIndex": len(INVOICE_HEADERS)},
            "rowProperties": {"headerColor": _NAVY, "firstBandColor": _WHITE, "secondBandColor": _LIGHT_GREY},
        }}},

        # Row banding — Monthly Summary
        {"addBanding": {"bandedRange": {
            "bandedRangeId": 2,
            "range": {"sheetId": 1, "startRowIndex": 0, "startColumnIndex": 0, "endColumnIndex": len(SUMMARY_HEADERS)},
            "rowProperties": {"headerColor": _NAVY, "firstBandColor": _WHITE, "secondBandColor": _LIGHT_GREY},
        }}},
    ]

    for i, w in enumerate(inv_col_widths):
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": 0, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": w},
            "fields": "pixelSize",
        }})

    for i, w in enumerate(sum_col_widths):
        requests.append({"updateDimensionProperties": {
            "range": {"sheetId": 1, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": w},
            "fields": "pixelSize",
        }})

    service.spreadsheets().batchUpdate(spreadsheetId=ss_id, body={"requests": requests}).execute()

    return ss_id, ss_url


def sync_invoices(user_id: str) -> dict:
    """Overwrites Invoices sheet rows with current business_records, preserving Payment Status."""
    result = supabase.table("google_integrations").select("*").eq("user_id", user_id).single().execute()
    if not result.data:
        raise ValueError("Google Sheets not connected")

    ss_id = result.data["spreadsheet_id"]
    creds = _get_credentials(user_id)
    service = build("sheets", "v4", credentials=creds)

    # Read existing Doc ID → Payment Status to preserve user-set statuses
    existing = service.spreadsheets().values().get(
        spreadsheetId=ss_id, range="Invoices!B:P"
    ).execute()
    payment_map: dict[str, str] = {}
    for row in (existing.get("values") or [])[1:]:
        doc_id = row[0] if len(row) > 0 else ""
        status = row[14] if len(row) > 14 else ""
        if doc_id and status:
            payment_map[doc_id] = status

    # Fetch all invoice records for this user
    records = (
        supabase.table("business_records")
        .select("*")
        .eq("user_id", user_id)
        .eq("record_type", "invoice")
        .order("created_at")
        .execute()
    )

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows = []
    for rec in records.data or []:
        d = rec.get("data") or {}
        doc_id = rec.get("source_id", "")
        rows.append([
            now,
            doc_id,
            d.get("invoice_number", ""),
            d.get("invoice_date", ""),
            d.get("due_date", ""),
            d.get("vendor_name", ""),
            d.get("vendor_tax_id", ""),
            d.get("bill_to_name", ""),
            d.get("currency", ""),
            d.get("subtotal") or "",
            d.get("discount_amount") or "",
            d.get("tax_rate") or "",
            d.get("tax_amount") or "",
            d.get("total_amount") or "",
            d.get("payment_terms", ""),
            payment_map.get(doc_id, "Pending"),
            d.get("notes", ""),
        ])

    # Clear existing data rows then write fresh
    service.spreadsheets().values().clear(
        spreadsheetId=ss_id, range="Invoices!A2:Q"
    ).execute()

    if rows:
        service.spreadsheets().values().update(
            spreadsheetId=ss_id,
            range="Invoices!A2",
            valueInputOption="USER_ENTERED",
            body={"values": rows},
        ).execute()

    # Rebuild Monthly Summary dynamically based on actual invoice dates
    current_year = datetime.now(timezone.utc).year
    years = []
    for rec in records.data or []:
        date_str = (rec.get("data") or {}).get("invoice_date", "")
        if date_str and len(date_str) >= 4:
            try:
                years.append(int(date_str[:4]))
            except ValueError:
                pass

    year_start = min(years) if years else current_year
    year_end = max(max(years) if years else current_year, current_year) + 1

    summary_rows = _build_summary_rows(year_start, year_end)
    service.spreadsheets().values().clear(
        spreadsheetId=ss_id, range="Monthly Summary!A2:F"
    ).execute()
    service.spreadsheets().values().update(
        spreadsheetId=ss_id,
        range="Monthly Summary!A2",
        valueInputOption="USER_ENTERED",
        body={"values": summary_rows},
    ).execute()

    # Mark all invoice records as synced
    supabase.table("business_records").update({"sync_status": "synced"}).eq("user_id", user_id).eq("record_type", "invoice").execute()

    supabase.table("google_integrations").update({
        "last_synced_at": datetime.now(timezone.utc).isoformat(),
    }).eq("user_id", user_id).execute()

    return {"synced": len(rows), "spreadsheet_id": ss_id}
