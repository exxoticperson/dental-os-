from __future__ import annotations

import gspread
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from dental_os.config import AppConfig


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class GoogleClients:
    def __init__(self, config: AppConfig) -> None:
        self.credentials = Credentials(
            token=None,
            refresh_token=config.google_oauth_refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=config.google_oauth_client_id,
            client_secret=config.google_oauth_client_secret,
            scopes=SCOPES,
        )
        self.credentials.refresh(Request())
        self.gspread_client = gspread.authorize(self.credentials)
        self.sheets_api = build("sheets", "v4", credentials=self.credentials, cache_discovery=False)
        self.drive_api = build("drive", "v3", credentials=self.credentials, cache_discovery=False)
