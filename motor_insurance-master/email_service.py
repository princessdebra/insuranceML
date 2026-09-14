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
from typing import List, Optional

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
        logger.info(f"Email sent to {to_email}: {subject}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {str(e)}")
        return False


def send_add_photos_email(
    to_email: str,
    member_name: str,
    member_id: str,
    claim_id: str,
    missing_field_labels: Optional[List[str]] = None,
) -> bool:
    """
    Sent when a claims analyst files on a member's behalf without photos --
    invites the member to add their own evidence to the claim they now have
    on file. Links straight to the member's claim page, with member_id
    attached so they don't need to already be logged in on this device (the
    portal's login is itself just member-ID lookup, no password -- this
    carries the same trust level, not a downgrade from it).

    missing_field_labels: human-readable labels for any claim fields the
    paper form left blank (e.g. "Estimated Repair Cost (KES)") -- the same
    member claim page lets them fill these in alongside the photos.
    """
    link = f"{FRONTEND_BASE_URL}/member/claim/{claim_id}?member_id={member_id}"
    subject = f"Add photos to your claim {claim_id}"

    missing_fields_html = ""
    if missing_field_labels:
        items = "".join(f"<li>{label}</li>" for label in missing_field_labels)
        missing_fields_html = f"""
      <p>
        A few details on the form were also left blank -- please fill these in too
        when you visit the link below:
      </p>
      <ul style="margin: 0 0 16px;">{items}</ul>
        """

    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color: #009476;">Claims Intelligence AI</h2>
      <p>Hi {member_name},</p>
      <p>
        Our claims team has filed claim <strong>{claim_id}</strong> on your behalf
        following your recent call. If you have photos of the damage, adding them
        now helps us process your claim faster.
      </p>
      {missing_fields_html}
      <p style="margin: 24px 0;">
        <a href="{link}" style="background: #009476; color: #ffffff; padding: 12px 24px;
           border-radius: 8px; text-decoration: none; font-weight: bold;">
          {"Complete My Claim" if missing_field_labels else "Add Photos to My Claim"}
        </a>
      </p>
      <p style="color: #666; font-size: 12px;">
        If the button doesn't work, copy this link into your browser: {link}
      </p>
    </div>
    """
    return send_email(to_email, subject, html_body)


def _claim_link(claim_id: str, member_id: str) -> str:
    return f"{FRONTEND_BASE_URL}/member/claim/{claim_id}?member_id={member_id}"


def send_claim_created_email(
    to_email: str, member_name: str, member_id: str, claim_id: str, assessor_name: Optional[str] = None,
) -> bool:
    """
    Sent the moment a claim exists and has been assigned -- member's first
    confirmation that their claim (or one an analyst filed for them) is
    real and someone is on it, whether they filed it themselves or an
    analyst filed it on their behalf.
    """
    link = _claim_link(claim_id, member_id)
    subject = f"Your claim {claim_id} has been received"
    assessor_line = f"<p>It has been assigned to <strong>{assessor_name}</strong> for review.</p>" if assessor_name else ""
    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color: #009476;">Claims Intelligence AI</h2>
      <p>Hi {member_name},</p>
      <p>
        We've received your claim <strong>{claim_id}</strong> and it's now on
        our assessor's dashboard for review.
      </p>
      {assessor_line}
      <p style="margin: 24px 0;">
        <a href="{link}" style="background: #009476; color: #ffffff; padding: 12px 24px;
           border-radius: 8px; text-decoration: none; font-weight: bold;">
          View My Claim
        </a>
      </p>
      <p style="color: #666; font-size: 12px;">
        If the button doesn't work, copy this link into your browser: {link}
      </p>
    </div>
    """
    return send_email(to_email, subject, html_body)


def send_inspection_scheduled_email(
    to_email: str, member_name: str, member_id: str, claim_id: str,
    inspection_date: Optional[str] = None, location: Optional[str] = None,
) -> bool:
    """Sent when the assessor schedules the physical inspection for a claim."""
    link = _claim_link(claim_id, member_id)
    subject = f"Inspection scheduled for your claim {claim_id}"
    details = "".join([
        f"<li><strong>Date:</strong> {inspection_date}</li>" if inspection_date else "",
        f"<li><strong>Location:</strong> {location}</li>" if location else "",
    ])
    details_html = f'<ul style="margin: 0 0 16px;">{details}</ul>' if details else ""
    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color: #009476;">Claims Intelligence AI</h2>
      <p>Hi {member_name},</p>
      <p>
        An inspection has been scheduled for your claim <strong>{claim_id}</strong>.
      </p>
      {details_html}
      <p style="margin: 24px 0;">
        <a href="{link}" style="background: #009476; color: #ffffff; padding: 12px 24px;
           border-radius: 8px; text-decoration: none; font-weight: bold;">
          View My Claim
        </a>
      </p>
      <p style="color: #666; font-size: 12px;">
        If the button doesn't work, copy this link into your browser: {link}
      </p>
    </div>
    """
    return send_email(to_email, subject, html_body)


def send_assessor_report_email(to_email: str, member_name: str, member_id: str, claim_id: str) -> bool:
    """Sent once the assessor has completed and submitted their on-site report."""
    link = _claim_link(claim_id, member_id)
    subject = f"Your claim {claim_id} has been inspected"
    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color: #009476;">Claims Intelligence AI</h2>
      <p>Hi {member_name},</p>
      <p>
        Our assessor has completed their inspection of your claim
        <strong>{claim_id}</strong>. It's now moving to the next stage of
        review.
      </p>
      <p style="margin: 24px 0;">
        <a href="{link}" style="background: #009476; color: #ffffff; padding: 12px 24px;
           border-radius: 8px; text-decoration: none; font-weight: bold;">
          View My Claim
        </a>
      </p>
      <p style="color: #666; font-size: 12px;">
        If the button doesn't work, copy this link into your browser: {link}
      </p>
    </div>
    """
    return send_email(to_email, subject, html_body)


def send_decision_email(
    to_email: str, member_name: str, member_id: str, claim_id: str,
    decision: str, reason: Optional[str] = None, payout_amount: Optional[float] = None,
) -> bool:
    """
    Sent when a claims analyst records the final PAY / DENY / ESCALATE
    decision. Wording stays neutral and professional for DENY/ESCALATE --
    this is a member-facing message, not an internal fraud/risk note, so no
    scoring or fraud language belongs here regardless of the decision.
    """
    link = _claim_link(claim_id, member_id)
    decision_upper = (decision or "").upper()

    if decision_upper == "PAY":
        subject = f"Your claim {claim_id} has been approved"
        amount_line = (
            f"<p>A payout of <strong>KES {payout_amount:,.0f}</strong> has been approved.</p>"
            if payout_amount else ""
        )
        headline = f"<p>Good news -- your claim <strong>{claim_id}</strong> has been approved for payment.</p>"
    elif decision_upper == "ESCALATE":
        subject = f"Your claim {claim_id} needs further review"
        amount_line = ""
        headline = (
            f"<p>Your claim <strong>{claim_id}</strong> needs a closer look before we can finalize it. "
            f"A specialist has been assigned and we'll be in touch.</p>"
        )
    else:  # DENY (or any other decision -- default to the safest wording)
        subject = f"Update on your claim {claim_id}"
        amount_line = ""
        headline = f"<p>We've completed our review of your claim <strong>{claim_id}</strong>.</p>"

    reason_html = f"<p><strong>Reason:</strong> {reason}</p>" if reason and decision_upper != "PAY" else ""

    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 480px; margin: 0 auto;">
      <h2 style="color: #009476;">Claims Intelligence AI</h2>
      <p>Hi {member_name},</p>
      {headline}
      {amount_line}
      {reason_html}
      <p style="margin: 24px 0;">
        <a href="{link}" style="background: #009476; color: #ffffff; padding: 12px 24px;
           border-radius: 8px; text-decoration: none; font-weight: bold;">
          View My Claim
        </a>
      </p>
      <p style="color: #666; font-size: 12px;">
        If the button doesn't work, copy this link into your browser: {link}
      </p>
    </div>
    """
    return send_email(to_email, subject, html_body)
