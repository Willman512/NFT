from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from gmail_spam_detector.config import Config

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def get_credentials(config: Config) -> Credentials:
    creds = None
    token_path = config.token_file
    token_path.parent.mkdir(parents=True, exist_ok=True)

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(config.credentials_file), SCOPES
            )
            creds = flow.run_local_server(port=0)

        token_path.write_text(creds.to_json())
        token_path.chmod(0o600)

    return creds


def get_gmail_service(config: Config):
    creds = get_credentials(config)
    return build("gmail", "v1", credentials=creds)


def check_auth_status(config: Config) -> dict:
    token_path = config.token_file
    if not token_path.exists():
        return {"authenticated": False, "reason": "No token file found. Run: auth"}

    try:
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if creds.valid:
            return {"authenticated": True, "expired": False}
        if creds.expired and creds.refresh_token:
            return {"authenticated": True, "expired": True, "refreshable": True}
        return {"authenticated": False, "reason": "Token invalid and cannot be refreshed"}
    except Exception as e:
        return {"authenticated": False, "reason": str(e)}
