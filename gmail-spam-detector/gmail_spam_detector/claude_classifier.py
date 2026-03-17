import json
import time
from dataclasses import dataclass

import anthropic

from gmail_spam_detector.gmail_client import EmailMessage

SYSTEM_PROMPT = """\
You are an email spam/junk classifier. Analyze emails and determine whether each is spam, junk, or legitimate.

Classify each email as one of:
- "spam": Unsolicited commercial email, phishing, scams, malware
- "junk": Low-value bulk mail, newsletters the user didn't sign up for, excessive promotions, social network notifications
- "legitimate": Genuine personal or business correspondence the user likely wants

Return ONLY a valid JSON array with one object per email in this exact format:
[
  {
    "id": "<message_id>",
    "classification": "spam" | "junk" | "legitimate",
    "confidence": 0.0,
    "reason": "<one sentence explanation>"
  }
]

Be conservative: when uncertain, classify as "legitimate".\
"""

CLAUDE_BATCH_SIZE = 15


@dataclass
class ClassificationResult:
    message_id: str
    classification: str  # "spam" | "junk" | "legitimate"
    confidence: float
    reason: str


def _build_user_prompt(emails: list[EmailMessage]) -> str:
    lines = [f"Classify the following {len(emails)} emails:\n"]
    for i, email in enumerate(emails, 1):
        lines.append(f"--- Email {i} ---")
        lines.append(f"ID: {email.id}")
        lines.append(f"From: {email.sender}")
        lines.append(f"Subject: {email.subject}")
        lines.append(f"Date: {email.date}")
        lines.append(f"Preview: {email.snippet}")
        body_excerpt = email.body_text[:500] if email.body_text else ""
        lines.append(f"Body excerpt: {body_excerpt}\n")
    return "\n".join(lines)


def classify_emails(
    client: anthropic.Anthropic,
    emails: list[EmailMessage],
    model: str,
) -> dict[str, ClassificationResult]:
    results: dict[str, ClassificationResult] = {}

    for i in range(0, len(emails), CLAUDE_BATCH_SIZE):
        batch = emails[i : i + CLAUDE_BATCH_SIZE]
        prompt = _build_user_prompt(batch)

        try:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()

            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```", 2)[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.rsplit("```", 1)[0].strip()

            parsed = json.loads(raw)
            for item in parsed:
                mid = item.get("id", "")
                if not mid:
                    continue
                results[mid] = ClassificationResult(
                    message_id=mid,
                    classification=item.get("classification", "legitimate"),
                    confidence=float(item.get("confidence", 0.0)),
                    reason=item.get("reason", ""),
                )
        except Exception:
            # Safe fallback: mark entire batch as legitimate on any error
            for email in batch:
                results[email.id] = ClassificationResult(
                    message_id=email.id,
                    classification="legitimate",
                    confidence=0.0,
                    reason="Classification failed; defaulting to legitimate",
                )

        # Polite rate-limit buffer between sub-batches
        if i + CLAUDE_BATCH_SIZE < len(emails):
            time.sleep(0.5)

    return results
