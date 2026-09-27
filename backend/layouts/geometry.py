"""Validation of a submitted layout, before anything is written.

Rectangles are integer canvas units with the origin at the top-left. A stall
must lie fully inside the canvas, and two stalls may touch along an edge but
must not overlap.
"""

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal

from core.exceptions import InvalidRequest
from layouts.models import MAX_CANVAS, PhysicalUnit
from layouts.money import MAX_PRICE_MINOR, SUPPORTED_CURRENCIES

MAX_STALLS = 500
MAX_LABEL = 40
MAX_DESCRIPTION = 500
MAX_PHYSICAL = Decimal("99999.99")
CURRENCY_PATTERN = re.compile(r"[A-Z]{3}")
# Used only while saving (services._write_stalls); real labels can't contain it.
TEMP_LABEL_PREFIX = "\u2063"


def _has_invisible(text: str) -> bool:
    return any(unicodedata.category(ch) in ("Cc", "Cf") for ch in text)


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def overlaps(self, other: "Rect") -> bool:
        """True when the interiors intersect; shared edges don't count."""
        return (
            self.x < other.right
            and other.x < self.right
            and self.y < other.bottom
            and other.y < self.bottom
        )


def overlapping_pairs(rects: list[Rect]) -> list[tuple[int, int]]:
    """Index pairs (i < j) of overlapping rectangles. Sorted by x, so each
    rectangle is only compared with those starting before it ends."""
    order = sorted(range(len(rects)), key=lambda i: rects[i].x)
    pairs = []
    for position, i in enumerate(order):
        for j in order[position + 1 :]:
            if rects[j].x >= rects[i].right:
                break
            if rects[i].overlaps(rects[j]):
                pairs.append((min(i, j), max(i, j)))
    return sorted(pairs)


def check_layout(canvas_width: int, canvas_height: int, currency: str, stalls: list[dict]) -> str:
    """Validate the whole submitted layout. Returns the normalized currency;
    raises InvalidRequest (``layout_invalid``) listing every problem."""
    problems: list[dict] = []

    def problem(message: str, **where) -> None:
        problems.append({**where, "message": message})

    currency = currency.strip().upper()
    if not CURRENCY_PATTERN.fullmatch(currency) or currency not in SUPPORTED_CURRENCIES:
        problem(f"currency must be one of {', '.join(SUPPORTED_CURRENCIES)}.", field="currency")
    for field, value in (("canvas_width", canvas_width), ("canvas_height", canvas_height)):
        if not 1 <= value <= MAX_CANVAS:
            problem(f"{field} must be between 1 and {MAX_CANVAS}.", field=field)
    if len(stalls) > MAX_STALLS:
        problem(f"A layout can have at most {MAX_STALLS} stalls.", field="stalls")

    labels: dict[str, int] = {}
    rects: list[Rect] = []
    for index, stall in enumerate(stalls):
        where = {"index": index, "stall_id": stall.get("id")}
        label = stall["label"]
        if not label or len(label) > MAX_LABEL:
            problem(f"Label must be 1-{MAX_LABEL} characters.", field="label", **where)
        elif _has_invisible(label):
            problem("Label can't contain control or invisible characters.", field="label", **where)
        elif label.casefold() in labels:
            problem("Another stall already uses this label.", field="label", **where)
        else:
            labels[label.casefold()] = index
        if len(stall["description"]) > MAX_DESCRIPTION:
            problem(
                f"Description must be at most {MAX_DESCRIPTION} characters.",
                field="description",
                **where,
            )
        rect = Rect(stall["x"], stall["y"], stall["width"], stall["height"])
        rects.append(rect)
        if rect.x < 0 or rect.y < 0:
            problem("Position can't be negative.", field="x", **where)
        if rect.width < 1 or rect.height < 1:
            problem("Width and height must be at least 1.", field="width", **where)
        elif rect.right > canvas_width or rect.bottom > canvas_height:
            problem("The stall must fit inside the canvas.", field="x", **where)
        if not 0 <= stall["price_minor"] <= MAX_PRICE_MINOR:
            problem(
                f"Price must be between 0 and {MAX_PRICE_MINOR} minor units.",
                field="price_minor",
                **where,
            )
        physical = (stall["physical_width"], stall["physical_depth"], stall["physical_unit"])
        if any(v not in (None, "") for v in physical):
            width, depth, unit = physical
            if width is None or depth is None or unit not in PhysicalUnit.values:
                problem(
                    "Give physical width, depth and unit together.", field="physical_width", **where
                )
            elif not (0 < width <= MAX_PHYSICAL and 0 < depth <= MAX_PHYSICAL):
                problem(
                    "Physical width and depth must be positive.", field="physical_width", **where
                )
            elif width.as_tuple().exponent < -2 or depth.as_tuple().exponent < -2:
                problem(
                    "Physical sizes allow at most 2 decimal places.",
                    field="physical_width",
                    **where,
                )

    for i, j in overlapping_pairs(rects):
        problem(
            f"Overlaps stall {stalls[i]['label']!r}.",
            field="x",
            index=j,
            stall_id=stalls[j].get("id"),
            other_index=i,
        )
    if problems:
        raise InvalidRequest("The layout has problems.", code="layout_invalid", details=problems)
    return currency
