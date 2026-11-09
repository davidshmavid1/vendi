"""Reading bookings. Bookings are only created by payments.services, in the
transaction that confirms their reservation.

    action                                     who
    list / view the business's bookings        vendor business OWNER, MEMBER
    list / view bookings for the org's dates   organization OWNER, ADMIN, STAFF
"""

from accounts.models import User
from bookings.models import Booking
from core.exceptions import NotFound
from organizations.permissions import membership_for as organization_membership
from vendors.permissions import membership_for as vendor_membership

_RELATED = (
    "offer__stall",
    "occurrence__market",
    "vendor_business",
    "payment_attempt",
    "cancellation__refund",
)


def list_for_business(actor: User, business_id: int, *, occurrence_id=None):
    vendor_membership(actor, business_id)
    queryset = Booking.objects.filter(vendor_business_id=business_id).select_related(*_RELATED)
    if occurrence_id:
        queryset = queryset.filter(occurrence_id=occurrence_id)
    return queryset


def get_for_business(actor: User, business_id: int, booking_id: int) -> Booking:
    booking = list_for_business(actor, business_id).filter(pk=booking_id).first()
    if booking is None:
        raise NotFound("Booking not found.")
    return booking


def list_for_organization(actor: User, organization_id: int, *, market_id=None, occurrence_id=None):
    organization_membership(actor, organization_id)
    queryset = Booking.objects.filter(
        occurrence__market__organization_id=organization_id
    ).select_related(*_RELATED)
    if market_id:
        queryset = queryset.filter(occurrence__market_id=market_id)
    if occurrence_id:
        queryset = queryset.filter(occurrence_id=occurrence_id)
    return queryset
