from django.conf import settings

from accounts.emails import frontend_link, send_email


def send_invitation_email(invitation, raw_token: str) -> bool:
    """Email the invitation link. The raw token exists only in this email."""
    link = frontend_link("/vendor-invitations/accept", token=raw_token)
    days = settings.VENDOR_INVITATION_MAX_AGE // 86400
    name = invitation.business.name
    body = (
        f"You've been invited to help manage the vendor business {name} on Vendi.\n\n"
        f"Accept within {days} days:\n\n{link}\n\n"
        "You'll need a Vendi account using this email address. If you don't have "
        "one yet, sign up and confirm your email first, then open the link again."
    )
    return send_email(
        invitation.email,
        kind="vendor invitation",
        ref=f"vendor invitation {invitation.pk}",
        subject=f"Join {name} on Vendi",
        body=body,
    )
