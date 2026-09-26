from datetime import datetime

from ninja import Field, Schema

from core.schemas import InputSchema

# Bounds keep absurd inputs out before hashing; real rules come from
# AUTH_PASSWORD_VALIDATORS.
Email = Field(..., max_length=254)
Password = Field(..., min_length=1, max_length=256)


class AccountOut(Schema):
    """The only account fields the API exposes. Never serialize the model."""

    id: int
    email: str
    email_verified: bool
    date_joined: datetime

    @staticmethod
    def resolve_email_verified(obj) -> bool:
        return obj.is_email_verified


class CsrfOut(Schema):
    csrf_token: str


class RegisterIn(InputSchema):
    email: str = Email
    password: str = Password


class RegisterOut(Schema):
    account: AccountOut
    verification_email_sent: bool


class VerifyEmailIn(InputSchema):
    token: str = Field(..., min_length=1, max_length=512)


class EmailIn(InputSchema):
    email: str = Email


class LoginIn(InputSchema):
    email: str = Email
    password: str = Password


class PasswordResetConfirmIn(InputSchema):
    uid: str = Field(..., min_length=1, max_length=64)
    token: str = Field(..., min_length=1, max_length=128)
    new_password: str = Password


class PasswordChangeIn(InputSchema):
    current_password: str = Password
    new_password: str = Password


class MessageOut(Schema):
    message: str
