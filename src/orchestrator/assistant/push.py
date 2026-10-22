"""Browser notifications with Web Push, so results reach people who are not on the site.

Create the keys once and keep them as secrets:

    python -m orchestrator.assistant.push

prints VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY. Set VAPID_SUBJECT to mailto:<your email>.
Without keys, notifications are simply skipped (results still arrive in the Inbox).
"""
import base64
import json
import logging
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

log = logging.getLogger(__name__)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def generate_keys() -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    private = _b64(key.private_numbers().private_value.to_bytes(32, "big"))
    public = _b64(key.public_key().public_bytes(serialization.Encoding.X962,
                                                serialization.PublicFormat.UncompressedPoint))
    return public, private


def public_key() -> str:
    return os.getenv("VAPID_PUBLIC_KEY", "").strip()


def enabled() -> bool:
    return bool(public_key() and os.getenv("VAPID_PRIVATE_KEY", "").strip())


def send(store, user_id: str, title: str, body: str, url: str = "/", tag: str = "") -> int:
    """Notify every browser the person turned notifications on in. Returns how many got it."""
    if not enabled():
        return 0
    from pywebpush import WebPushException, webpush

    payload = json.dumps({"title": title[:80], "body": body[:240], "url": url, "tag": tag or url})
    sent = 0
    for sub in store.pushes_for(user_id):
        try:
            webpush(
                subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]},
                data=payload,
                vapid_private_key=os.environ["VAPID_PRIVATE_KEY"].strip(),
                vapid_claims={"sub": os.getenv("VAPID_SUBJECT", "mailto:relay@example.com")},
                ttl=24 * 3600,
            )
            sent += 1
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):  # the browser unsubscribed
                store.delete_push(sub["endpoint"])
            else:
                log.warning("push failed: %s", e)
    return sent


if __name__ == "__main__":
    pub, priv = generate_keys()
    print(f"VAPID_PUBLIC_KEY={pub}\nVAPID_PRIVATE_KEY={priv}\nVAPID_SUBJECT=mailto:you@example.com")
