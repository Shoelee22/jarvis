"""Contact actions dynamic provider (Phase 7, Worker 4).

One lazily-materialized Tool per contact × channel × message template:
``contact.<channel>.<slug>.<template>`` where channel is one of
call | sms | whatsapp | email | telegram.

Backends are REAL when configured, honest {"error"} when not -- a send is
NEVER faked. All handlers take a dict and return a dict; they never raise.

Slug rule: name -> re.sub(r"[^a-z0-9]+","_", name.lower()).strip("_").
Slug collisions keep the FIRST contact (insertion order) -- this is by
design so expand()/resolve() stay deterministic and cheap.
"""
from __future__ import annotations

import base64
import re
import smtplib
import urllib.parse
import urllib.request
from email.message import EmailMessage

from ..base import Tool
from ..builtin.comms import _contacts_conn, _smtp_config
from ...security import egress
from .config import section
from .providers import Provider

# ------------------------------------------------------------------ templates
TEMPLATES: dict[str, str] = {
    "birthday": "Happy birthday, {name}! Hope you have a wonderful day filled with joy.",
    "checkin": "Hi {name}, just checking in to see how you are doing. Hope all is well with you.",
    "reminder": "Hi {name}, this is a friendly reminder about our upcoming commitment. Please let me know if you need to reschedule.",
    "thankyou": "Thank you so much, {name}, for your time and help. It is truly appreciated.",
    "invoice_nudge": "Hi {name}, just a gentle nudge that your invoice is now due. Please let me know once you have had a chance to review it.",
    "meeting_confirm": "Hi {name}, confirming our meeting as planned. Looking forward to seeing you. Please reply if anything changes.",
    "congrats": "Congratulations, {name}! That is fantastic news, and you have earned every bit of it.",
    "get_well": "Hi {name}, wishing you a speedy recovery. Take all the rest you need and let me know if I can help with anything.",
    "festival_greeting": "Warm festival greetings to you and your family, {name}. May this season bring you happiness and good health.",
    "followup": "Hi {name}, following up on our last conversation. I wanted to see if you had any questions or needed anything further.",
    "apology": "Hi {name}, I sincerely apologize for the inconvenience. I value our relationship and will make this right.",
    "referral_ask": "Hi {name}, I hope you are doing well. If you know anyone who could use my help, I would be grateful for an introduction.",
    "quote_followup": "Hi {name}, just following up on the quote I sent over. Happy to walk you through it or adjust anything.",
    "payment_reminder": "Hi {name}, this is a polite reminder that a payment is coming due. Please reach out if you need the details again.",
    "appointment_reminder": "Hi {name}, a quick reminder of your upcoming appointment. Please let me know if you need to change the time.",
    "welcome": "Welcome aboard, {name}! I am really glad to have you with us and look forward to working together.",
    "goodbye": "Take care, {name}. It was a pleasure, and I wish you the very best ahead.",
    "referral_thanks": "Hi {name}, thank you so much for the referral. I truly appreciate you thinking of me.",
    "feedback_ask": "Hi {name}, I would love your feedback on how things went. Your honest thoughts would really help me improve.",
    "holiday_greeting": "Happy holidays, {name}! Wishing you and your loved ones a restful and joyful season.",
    "new_year": "Happy New Year, {name}! Wishing you health, happiness, and success in the year ahead.",
    "diwali_greeting": "Happy Diwali, {name}! May this festival of lights bring prosperity and joy to you and your family.",
    "condolence": "Dear {name}, please accept my heartfelt condolences. I am thinking of you and your family during this difficult time.",
    "encouragement": "Hi {name}, I just wanted to say I believe in you. Keep going, and know that you have my support.",
    "lunch_invite": "Hi {name}, would you like to grab lunch together sometime soon? It would be great to catch up.",
    "call_back": "Hi {name}, sorry I missed your call. I will call you back shortly, but feel free to ring me anytime.",
    "missed_you": "Hi {name}, I missed you and thought I would say hello. Hope everything is going well on your end.",
    "safe_travels": "Hi {name}, wishing you safe travels! Have a great trip and let me know when you land.",
    "quick_hello": "Hi {name}, just saying a quick hello. Hope you are having a good day.",
    "milestone": "Hi {name}, reaching this milestone is a big deal. I am proud of you and cheering you on for what comes next.",
}

_CHANNELS = ("call", "sms", "whatsapp", "email", "telegram")

_TWILIO_DEFAULTS = {
    "enabled": False, "account_sid": "", "auth_token": "",
    "from_number": "", "whatsapp_from": "",
}
_TELEGRAM_DEFAULTS = {
    "enabled": False, "bot_token": "", "default_chat_id": "",
}

_NOT_CONFIGURED = "not configured: fill in the {section}: section of tools_config.dynamic.yaml"


# ------------------------------------------------------------------- slugging
def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def _contact_by_slug(slug: str) -> dict | None:
    """First contact (insertion order) whose name slugs to `slug`."""
    try:
        with _contacts_conn() as conn:
            rows = conn.execute(
                "SELECT id, name, phone, email, notes FROM contacts ORDER BY id"
            ).fetchall()
    except Exception:
        return None
    for r in rows:
        if _slug(r[1]) == slug:
            return {"id": r[0], "name": r[1], "phone": r[2], "email": r[3], "notes": r[4]}
    return None


def _contact_count() -> int:
    try:
        with _contacts_conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
    except Exception:
        return 0


# ------------------------------------------------------------------ backends
def _twilio_cfg() -> dict:
    return section("twilio", _TWILIO_DEFAULTS)


def _telegram_cfg() -> dict:
    return section("telegram", _TELEGRAM_DEFAULTS)


def _twilio_post(path: str, params: dict) -> dict:
    """POST to the Twilio API with HTTP basic auth. Returns parsed dict or raises."""
    cfg = _twilio_cfg()
    sid = str(cfg.get("account_sid", "")).strip()
    token = str(cfg.get("auth_token", "")).strip()
    if not cfg.get("enabled") or not sid or not token:
        raise RuntimeError(_NOT_CONFIGURED.format(section="twilio"))
    host = "api.twilio.com"
    if not egress.check("contact.twilio", host):
        raise RuntimeError(f"egress to {host} blocked by policy")
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        f"https://{host}/2010-04-01/Accounts/{sid}/{path}",
        data=data,
        headers={
            "Authorization": "Basic " + base64.b64encode(
                f"{sid}:{token}".encode()).decode(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    import json
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode() or "{}")


def _send_call(contact: dict, text: str) -> dict:
    phone = (contact.get("phone") or "").strip()
    if not phone:
        return {"error": f"contact '{contact['name']}' has no phone number"}
    cfg = _twilio_cfg()
    from_number = str(cfg.get("from_number", "")).strip()
    if not cfg.get("enabled") or not from_number:
        return {"error": _NOT_CONFIGURED.format(section="twilio")}
    try:
        res = _twilio_post("Calls.json", {
            "To": phone,
            "From": from_number,
            "Twiml": f"<Response><Say>{text}</Say></Response>",
        })
        return {"sent": True, "channel": "call", "to": phone,
                "sid": res.get("sid", "")}
    except RuntimeError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"twilio call failed: {type(e).__name__}: {e}"}


def _send_sms(contact: dict, text: str) -> dict:
    phone = (contact.get("phone") or "").strip()
    if not phone:
        return {"error": f"contact '{contact['name']}' has no phone number"}
    cfg = _twilio_cfg()
    from_number = str(cfg.get("from_number", "")).strip()
    if not cfg.get("enabled") or not from_number:
        return {"error": _NOT_CONFIGURED.format(section="twilio")}
    try:
        res = _twilio_post("Messages.json", {
            "To": phone, "From": from_number, "Body": text,
        })
        return {"sent": True, "channel": "sms", "to": phone,
                "sid": res.get("sid", "")}
    except RuntimeError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"twilio sms failed: {type(e).__name__}: {e}"}


def _send_whatsapp(contact: dict, text: str) -> dict:
    phone = (contact.get("phone") or "").strip()
    if not phone:
        return {"error": f"contact '{contact['name']}' has no phone number"}
    cfg = _twilio_cfg()
    wa_from = str(cfg.get("whatsapp_from", "")).strip()
    if not cfg.get("enabled") or not wa_from:
        return {"error": _NOT_CONFIGURED.format(section="twilio")}
    try:
        res = _twilio_post("Messages.json", {
            "To": f"whatsapp:{phone}", "From": wa_from, "Body": text,
        })
        return {"sent": True, "channel": "whatsapp", "to": f"whatsapp:{phone}",
                "sid": res.get("sid", "")}
    except RuntimeError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"twilio whatsapp failed: {type(e).__name__}: {e}"}


def _send_email(contact: dict, template_key: str, text: str) -> dict:
    email_addr = (contact.get("email") or "").strip()
    if not email_addr:
        return {"error": f"contact '{contact['name']}' has no email address"}
    cfg = _smtp_config()  # comms.py's smtp section (tools_config.yaml)
    if not cfg.get("enabled") or not cfg.get("host"):
        return {"error": "not configured: fill in the smtp: section of tools_config.yaml"}
    host = cfg["host"]
    if not egress.check("contact.email", host):
        return {"error": f"egress to {host} blocked by policy"}
    try:
        port = int(cfg.get("port", 587))
    except (TypeError, ValueError):
        return {"error": "smtp port must be a number"}
    try:
        msg = EmailMessage()
        msg["From"] = cfg.get("from_addr") or cfg.get("username") or ""
        msg["To"] = email_addr
        msg["Subject"] = template_key.replace("_", " ").title()
        msg.set_content(text)
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            if cfg.get("use_tls", True):
                smtp.starttls()
            if cfg.get("username"):
                smtp.login(cfg["username"], cfg.get("password", ""))
            smtp.send_message(msg)
        return {"sent": True, "channel": "email", "to": email_addr}
    except Exception as e:  # never leak the password in the error
        return {"error": f"smtp send failed: {type(e).__name__}: {e}"}


def _send_telegram(contact: dict, text: str, chat_id: str | None) -> dict:
    cfg = _telegram_cfg()
    bot_token = str(cfg.get("bot_token", "")).strip()
    if not cfg.get("enabled") or not bot_token:
        return {"error": _NOT_CONFIGURED.format(section="telegram")}
    cid = (chat_id or "").strip() or str(cfg.get("default_chat_id", "")).strip()
    if not cid:
        return {"error": "telegram chat_id required: pass chat_id or set telegram.default_chat_id"}
    import json
    payload = json.dumps({"chat_id": cid, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=payload, headers={"Content-Type": "application/json"})
    try:
        if not egress.check("contact.telegram", "api.telegram.org"):
            return {"error": "egress to api.telegram.org blocked by policy"}
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode() or "{}")
        if not body.get("ok"):
            return {"error": f"telegram send failed: {body.get('description', 'unknown error')}"}
        return {"sent": True, "channel": "telegram", "to": cid,
                "message_id": body.get("result", {}).get("message_id", "")}
    except Exception as e:
        return {"error": f"telegram send failed: {type(e).__name__}: {e}"}


# ------------------------------------------------------------------ provider
class ContactsActionsProvider(Provider):
    """Dynamic contact-action tools: contact.<channel>.<slug>.<template>."""

    namespace = "contact"

    def expand(self) -> int:
        """Exact addressable count: live contacts x 5 channels x 30 templates."""
        return _contact_count() * len(_CHANNELS) * len(TEMPLATES)

    def resolve(self, name: str) -> Tool | None:
        if not self._owns(name):
            return None
        parts = self._local(name).split(".")
        if len(parts) != 3:
            return None
        channel, slug, template = parts
        if channel not in _CHANNELS:
            return None
        if template not in TEMPLATES:
            return None
        contact = _contact_by_slug(slug)
        if contact is None:
            return None
        return self._build_tool(name, channel, contact, template)

    def _build_tool(self, name: str, channel: str, contact: dict,
                    template: str) -> Tool:
        cname = contact["name"]

        def handler(args: dict) -> dict:
            try:
                args = args or {}
                template_text = TEMPLATES[template]
                text = str(args.get("text_override") or template_text).format(name=cname)
                to_override = str(args.get("to_override") or "").strip()
                if to_override:
                    if channel == "email":
                        contact2 = dict(contact, email=to_override)
                    else:
                        contact2 = dict(contact, phone=to_override)
                else:
                    contact2 = contact
                if channel == "call":
                    return _send_call(contact2, text)
                if channel == "sms":
                    return _send_sms(contact2, text)
                if channel == "whatsapp":
                    return _send_whatsapp(contact2, text)
                if channel == "email":
                    return _send_email(contact2, template, text)
                if channel == "telegram":
                    return _send_telegram(contact2, text,
                                          args.get("chat_id"))
                return {"error": f"unknown channel '{channel}'"}
            except Exception as e:  # handlers NEVER raise
                return {"error": f"contact action failed: {type(e).__name__}: {e}"}

        return Tool(
            name=name,
            description=f"Send the '{template}' message to {cname} via {channel}.",
            schema={"text_override": "string?", "to_override": "string?",
                    "chat_id": "string?"},
            handler=handler,
            risk="high",
            needs_network=True,
        )

    def sample_names(self, n: int = 5) -> list[str]:
        try:
            with _contacts_conn() as conn:
                rows = conn.execute(
                    "SELECT name FROM contacts ORDER BY id LIMIT ?",
                    (max(1, n),)).fetchall()
        except Exception:
            return []
        names: list[str] = []
        for (name,) in rows:
            names.append(f"{self.namespace}.sms.{_slug(name)}.checkin")
            if len(names) >= n:
                break
        return names[:n]
