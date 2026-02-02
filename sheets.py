from __future__ import annotations
import os
from typing import List
import gspread
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

class SheetWriter:
    """
    OAuth (installed app) Sheets client.
    First run opens a browser and writes token.json.
    """
    def __init__(self, sheet_id: str, tab_name: str = "Prospects", token_path: str = "token.json"):
        if not sheet_id:
            raise ValueError("sheet_id is required")

        oauth_path = os.getenv("GOOGLE_OAUTH_CREDENTIALS")
        if not oauth_path or not os.path.isfile(oauth_path):
            raise FileNotFoundError("Set GOOGLE_OAUTH_CREDENTIALS to the path of your OAuth client JSON (Desktop app).")

        creds = None
        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(oauth_path, SCOPES)
                creds = flow.run_local_server(port=0)
            with open(token_path, "w") as f:
                f.write(creds.to_json())

        client = gspread.authorize(creds)
        self.sheet = client.open_by_key(sheet_id)
        try:
            self.worksheet = self.sheet.worksheet(tab_name)
        except gspread.WorksheetNotFound:
            self.worksheet = self.sheet.add_worksheet(title=tab_name, rows=2000, cols=10)
            # No header write, since you're starting at C5

    # ---------- NEW HELPERS ----------
    @staticmethod
    def _col_letter(col_idx: int) -> str:
        """1 -> A, 2 -> B, 3 -> C, ..."""
        s = ""
        while col_idx:
            col_idx, r = divmod(col_idx - 1, 26)
            s = chr(65 + r) + s
        return s

    def _next_empty_row_cde(self, start_row: int = 5) -> int:
        """
        Find the first row >= start_row where columns C, D, and E are all empty.
        Ignores other columns (e.g., B can have data).
        """
        # Quick guess: one after the last non-empty among C/D/E
        len_c = len(self.worksheet.col_values(3))  # up to last non-empty in col C
        len_d = len(self.worksheet.col_values(4))
        len_e = len(self.worksheet.col_values(5))
        r = max(start_row, max(len_c, len_d, len_e) + 1)

        # Verify the guessed row is truly empty across C..E; if not, scan downward
        while True:
            vals = self.worksheet.get(f"C{r}:E{r}")  # returns [[]] or [] if empty
            row = vals[0] if vals else []
            # Treat missing cells as empty
            cde_empty = all((i >= len(row) or str(row[i]).strip() == "") for i in range(3))
            if cde_empty:
                return r
            r += 1

    # ---------- UPDATED APPEND ----------
    def append_rows(self, rows: List[List[str]]):
        """
        Appends rows to the first available row >= 5 where C/D/E are empty.
        Each item in `rows` is [Name, Industry, Link] -> written to C, D, E.
        """
        if not rows:
            return

        start_row = self._next_empty_row_cde(start_row=5)
        start_col = 3  # column C
        end_col = start_col + len(rows[0]) - 1  # -> E for 3 cols

        start_col_letter = self._col_letter(start_col)
        end_col_letter = self._col_letter(end_col)
        cell_range = f"{start_col_letter}{start_row}:{end_col_letter}{start_row + len(rows) - 1}"

        self.worksheet.update(cell_range, rows, value_input_option="RAW")
