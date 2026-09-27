from django.conf import settings

from accounts.emails import frontend_link, send_email


def send_invitation_email(invitation, raw_token: str) -> bool:
    """Email the invitation link. The raw token exists only in this email."""
    link = frontend_link("/invitations/accept", token=raw_token)
    days = settings.ORGANIZATION_INVITATION_MAX_AGE // 86400
    body = (
        f"You've been invited to join {invitation.organization.name} on Vendi "
        f"as {invitation.get_role_display().lower()}.\n\n"
        f"Accept within {days} days:\n\n{link}\n\n"
        "You'll need a Vendi account using this email address. If you don't have "
        "one yet, sign up and confirm your email first, then open the link again."
    )
    return send_email(
        invitation.email,
        kind="invitation",
        ref=f"invitation {invitation.pk}",
        subject=f"Join {invitation.organization.name} on Vendi",
        body=body,
    )
