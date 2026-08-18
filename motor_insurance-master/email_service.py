"""
Outbound email via Gmail SMTP -- used to notify a member when an analyst has
filed a claim on their behalf without photos, so they can add their own
evidence afterward (see /api/analysis/claim/{claim_id}/notify-member and the
add-photos flow in routes.py).

Configure via env vars:
    GMAIL_ADDRESS       the Gmail account to send from
    GMAIL_APP_PASSWORD  a Google Account App Password (NOT the account's
                         regular login password -- Gmail rejects SMTP auth
                         with the regular password entirely)
    FRONTEND_BASE_URL   base URL of the member portal, used to build the
                         link in the email (default: devserver address)

If GMAIL_ADDRESS/GMAIL_APP_PASSWORD aren't set, send_email() logs a warning
and returns False rather than raising -- callers should treat email as
best-effort, not a hard dependency for filing a claim.
"""
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "http://41.90.122.129:2002")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def is_configured() -> bool:
    return bool(GMAIL_ADDRESS and GMAIL_APP_PASSWORD)


def send_email(to_email: str, subject: str, html_body: str) -> bool:
    if not is_configured():
        logger.warning(
            "Email not sent -- GMAIL_ADDRESS/GMAIL_APP_PASSWORD not configured. "
            f"(Would have sent '{subject}' to {to_email})"
        )
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, [to_email], msg.as_string())
        logger.info(f"📧 Email sent to {to_email}: {subject}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to send email to {to_email}: {str(e)}")
        return False


def send_add_photos_email(to_email: str, member_name: str, member_id: str, claim_id: str) -> bool:
    """
    Sent when a claims analyst files on a member's behalf without photos --
    invites the member to add their own evidence to the claim they now have
    on file. Links straight to the member's claim page, with member_id
    attached so they don't need to already be logged in on this device (the
    portal's login is itself just member-ID lookup, no password -- this
    carries the same trust level, not a downgrade from it).
    """
    link = f"{FRONTEND_BASE_URL}/member/claim/{claim_id}?member_id={member_id}"
    subject = f"Add photos to your claim {claim_id}"
    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color: #009476;">Claims Intelligence AI</h2>
      <p>Hi {member_name},</p>
      <p>
        Our claims team has filed claim <strong>{claim_id}</strong> on your behalf
        following your recent call. If you have photos of the damage, adding them
        now helps us process your claim faster.
      </p>
      <p style="margin: 24px 0;">
        <a href="{link}" style="background: #009476; color: #ffffff; padding: 12px 24px;
           border-radius: 8px; text-decoration: none; font-weight: bold;">
          Add Photos to My Claim
        </a>
      </p>
      <p style="color: #666; font-size: 12px;">
        If the button doesn't work, copy this link into your browser: {link}
      </p>
    </div>
    """
    return send_email(to_email, subject, html_body)
