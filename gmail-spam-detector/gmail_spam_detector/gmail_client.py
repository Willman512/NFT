import base64
import email.utils
from dataclasses import dataclass, field
from typing import Optional

from googleapiclient.errors import HttpError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


@dataclass
class EmailMessage:
    id: str
    thread_id: str
    subject: str
    sender: str
    date: str
    snippet: str
    body_text: str
    labels: list = field(default_factory=list)


def _is_retryable(exc: Exception) -> bool:
    return isinstance(exc, HttpError) and exc.resp.status in (429, 500, 503)


def list_messages(service, query: str = "in:inbox", max_results: int = 200) -> list[str]:
    ids = []
    page_token = None

    while len(ids) < max_results:
        batch_max = min(500, max_results - len(ids))
        params = {
            "userId": "me",
            "q": query,
            "maxResults": batch_max,
        }
        if page_token:
            params["pageToken"] = page_token

        result = service.users().messages().list(**params).execute()
        messages = result.get("messages", [])
        ids.extend(m["id"] for m in messages)

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return ids[:max_results]


def _extract_body(payload: dict) -> str:
    body = ""
    mime_type = payload.get("mimeType", "")

    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            body = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    elif mime_type.startswith("multipart/"):
        for part in payload.get("parts", []):
            body = _extract_body(part)
            if body:
                break

    return body


def _get_header(headers: list, name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def get_message(service, message_id: str) -> EmailMessage:
    msg = service.users().messages().get(
        userId="me", id=message_id, format="full"
    ).execute()

    payload = msg.get("payload", {})
    headers = payload.get("headers", [])

    subject = _get_header(headers, "Subject") or "(no subject)"
    raw_sender = _get_header(headers, "From") or ""
    _, sender_email = email.utils.parseaddr(raw_sender)
    sender = sender_email or raw_sender
    date = _get_header(headers, "Date") or ""
    snippet = msg.get("snippet", "")

    body_text = _extract_body(payload)[:2000]

    return EmailMessage(
        id=msg["id"],
        thread_id=msg.get("threadId", ""),
        subject=subject,
        sender=sender,
        date=date,
        snippet=snippet,
        body_text=body_text,
        labels=msg.get("labelIds", []),
    )


def batch_get_messages(service, message_ids: list[str]) -> list[EmailMessage]:
    results = []
    # Gmail BatchHttpRequest limit is 100 per batch
    for i in range(0, len(message_ids), 100):
        chunk = message_ids[i : i + 100]
        batch = service.new_batch_http_request()
        chunk_results: dict[str, Optional[EmailMessage]] = {}

        def make_callback(mid):
            def callback(request_id, response, exception):
                if exception:
                    return
                payload = response.get("payload", {})
                headers = payload.get("headers", [])
                subject = _get_header(headers, "Subject") or "(no subject)"
                raw_sender = _get_header(headers, "From") or ""
                _, sender_email = email.utils.parseaddr(raw_sender)
                sender = sender_email or raw_sender
                date = _get_header(headers, "Date") or ""
                snippet = response.get("snippet", "")
                body_text = _extract_body(payload)[:2000]
                chunk_results[mid] = EmailMessage(
                    id=response["id"],
                    thread_id=response.get("threadId", ""),
                    subject=subject,
                    sender=sender,
                    date=date,
                    snippet=snippet,
                    body_text=body_text,
                    labels=response.get("labelIds", []),
                )
            return callback

        for mid in chunk:
            batch.add(
                service.users().messages().get(userId="me", id=mid, format="full"),
                callback=make_callback(mid),
            )
        batch.execute()
        results.extend(v for v in chunk_results.values() if v is not None)

    return results


@retry(
    retry=retry_if_exception_type(HttpError),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    stop=stop_after_attempt(5),
)
def trash_message(service, message_id: str) -> dict:
    return service.users().messages().trash(userId="me", id=message_id).execute()


def trash_messages_batch(
    service, message_ids: list[str]
) -> tuple[list[str], list[str]]:
    trashed = []
    failed = []
    for mid in message_ids:
        try:
            trash_message(service, mid)
            trashed.append(mid)
        except Exception:
            failed.append(mid)
    return trashed, failed
