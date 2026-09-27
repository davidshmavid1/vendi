from datetime import datetime
from typing import Literal

from ninja import Field, Schema

from core.schemas import InputSchema
from markets.schemas import PublicOccurrenceOut

ApplicationStatusName = Literal["SUBMITTED", "APPROVED", "REJECTED", "WITHDRAWN"]
QuestionType = Literal["short_text", "long_text", "single_choice", "acknowledgement"]
IntakeStateName = Literal["open", "not_open_yet", "closed", "not_accepting"]
Answer = str | bool | None


class QuestionIn(InputSchema):
    id: str = Field(..., min_length=1, max_length=40)
    type: QuestionType
    label: str = Field(..., max_length=300)
    required: bool = False
    choices: list[str] = Field(default_factory=list, max_length=20)


class Question(Schema):
    id: str
    type: QuestionType
    label: str
    required: bool
    choices: list[str]


# --- Organizer intake configuration ---------------------------------------------------


class IntakeIn(InputSchema):
    enabled: bool
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    instructions: str = Field("", max_length=5000)
    questions: list[QuestionIn] = Field(default_factory=list, max_length=20)


class IntakeOut(Schema):
    occurrence_id: int
    configured: bool
    enabled: bool
    opens_at: datetime | None
    closes_at: datetime | None
    instructions: str
    questions_version: int
    questions: list[Question]
    state: IntakeStateName
    updated_at: datetime | None


# --- Public ---------------------------------------------------------------------------


class PublicIntakeOut(Schema):
    """What a vendor needs to apply. Questions are shown only while the date
    accepts applications (open or opening later)."""

    occurrence: PublicOccurrenceOut
    market_name: str
    organizer_name: str
    state: IntakeStateName
    opens_at: datetime | None
    closes_at: datetime | None
    instructions: str
    questions_version: int | None
    questions: list[Question]


class IntakeWindow(Schema):
    occurrence_id: int
    state: IntakeStateName
    opens_at: datetime | None
    closes_at: datetime | None


class IntakeWindowList(Schema):
    items: list[IntakeWindow]


# --- Applications ---------------------------------------------------------------------


class ApplicationIn(InputSchema):
    occurrence_id: int
    questions_version: int
    answers: dict[str, Answer] = Field(default_factory=dict)


class DecisionIn(InputSchema):
    message: str = Field("", max_length=2000)


class VendorSnapshot(Schema):
    name: str
    category: str
    description: str
    contact_email: str
    phone: str
    website: str
    city: str
    region: str


class ApplicationOccurrence(Schema):
    id: int
    market_id: int
    market_name: str
    starts_at: datetime
    ends_at: datetime
    timezone: str
    status: str


class ApplicationFields(Schema):
    id: int
    status: ApplicationStatusName
    vendor_business_id: int
    occurrence: ApplicationOccurrence
    questions_version: int
    questions: list[Question]
    answers: dict[str, Answer]
    vendor_snapshot: VendorSnapshot
    submitted_at: datetime
    decided_at: datetime | None
    decision_message: str
    withdrawn_at: datetime | None


class VendorApplicationOut(ApplicationFields):
    """The vendor's view: no reviewer identities or organization internals."""

    organizer_name: str


class ApplicationHistoryEntry(Schema):
    from_status: str
    to_status: ApplicationStatusName
    actor_user_id: int
    at: datetime


class OrganizerApplicationOut(ApplicationFields):
    submitted_by_user_id: int
    decided_by_user_id: int | None
    withdrawn_by_user_id: int | None
    history: list[ApplicationHistoryEntry]


class OrganizerApplicationSummary(Schema):
    id: int
    status: ApplicationStatusName
    vendor_business_id: int
    vendor_name: str
    occurrence: ApplicationOccurrence
    submitted_at: datetime
    decided_at: datetime | None


class VendorApplicationPage(Schema):
    items: list[VendorApplicationOut]
    next_cursor: int | None


class OrganizerApplicationPage(Schema):
    items: list[OrganizerApplicationSummary]
    next_cursor: int | None
