from __future__ import annotations

import os

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow


load_dotenv()


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def main() -> None:
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise ValueError("Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET first.")

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        SCOPES,
    )

    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
        open_browser=False,
        authorization_prompt_message="Open this URL in your browser:\n{url}\n",
        success_message="Auth received. You can close this tab.",
    )
    print("GOOGLE_OAUTH_REFRESH_TOKEN=" + (creds.refresh_token or ""))


if __name__ == "__main__":
    main()
