"""Private adapter. Nothing from upstream is forwarded directly to clients."""
import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser

from curl_cffi import requests
from curl_cffi.requests.exceptions import RequestException

BASE_URL = "https://leofame.com"
# Add a combination only after its route, quantity and response contract are verified.
ROUTES = {
    ("tiktok", "views"): ("free-tiktok-views", "200"),
    ("tiktok", "likes"): ("free-tiktok-likes", None),
}


@dataclass(frozen=True)
class Outcome:
    status: str
    retry_after: int | None = None


class QuantityParser(HTMLParser):
    quantity = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        value = attrs.get("value") or ""
        if tag == "input" and attrs.get("name") == "quantity" and value.isdigit():
            self.quantity = value


def retry_seconds(value):
    # Only explicit seconds; never reflect upstream text or guess cooldown duration.
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 0 < value <= 86400:
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 5:
        number = int(value)
        return number if 0 < number <= 86400 else None
    return None


def interpret(response):
    try:
        body = response.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        body = {}
    retry = retry_seconds(response.headers.get("Retry-After"))
    retry = retry or retry_seconds(body.get("retry_after_seconds")) or retry_seconds(body.get("retry_after"))
    # A small, explicit vocabulary: ambiguous values must never become success.
    state = body.get("status")
    state = state.strip().lower() if isinstance(state, str) else ""
    code = body.get("code")
    code = code.strip().lower() if isinstance(code, str) else ""
    message = body.get("message")
    message = message[:2000].lower() if isinstance(message, str) else ""
    limited = state in {"limited", "rate_limited", "cooldown", "limit"} or code in {"rate_limited", "cooldown", "limit_reached"}
    limited = limited or (state not in {"pending", "queued", "processing", "accepted", "success", "successful", "completed"} and body.get("success") is not True and bool(re.search(
        r"\b(cooldown|rate limit|too many requests|limit reached|try again in)\b"
        r"|\bwait\s+\d+\s*(seconds?|minutes?|hours?)\b", message)))
    if response.status_code == 429 or limited:
        return Outcome("rate_limited", retry)
    if response.status_code >= 500 or response.status_code in {401, 403}:
        return Outcome("unavailable")
    if not 200 <= response.status_code < 300:
        return Outcome("unknown")
    if body.get("success") is False or state in {"error", "failed", "failure", "rejected"}:
        return Outcome("failed")
    if state in {"pending", "queued", "processing", "accepted"}:
        return Outcome("pending")
    if body.get("success") is True or state in {"success", "successful", "completed"}:
        return Outcome("success")
    return Outcome("unknown")


def execute(platform, service, url):
    path, quantity = ROUTES[(platform, service)]
    page_url = f"{BASE_URL}/{path}"
    submitted = False
    try:
        with requests.Session(impersonate="chrome124") as session:
            page = session.get(page_url, timeout=25, allow_redirects=False)
            if page.status_code == 429:
                return Outcome("rate_limited", retry_seconds(page.headers.get("Retry-After")))
            if page.status_code != 200:
                return Outcome("unavailable")
            token = session.cookies.get("token")
            if not token:
                return Outcome("unavailable")
            if quantity is None:
                parser = QuantityParser()
                parser.feed(page.text)
                quantity = parser.quantity
                if quantity is None:
                    return Outcome("unavailable")
            payload = {"token": token, "timezone_offset": "Asia/Jakarta", "timezone_offset_2": "abcd", "free_link": url, "quantity": quantity}
            time.sleep(2)
            submitted = True
            response = session.post(f"{page_url}?api=1", data=payload,
                headers={"Origin": BASE_URL, "Referer": page_url, "Accept": "application/json"},
                timeout=25, allow_redirects=False)
            return interpret(response)
    except RequestException:
        return Outcome("unknown" if submitted else "unavailable")
