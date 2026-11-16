"""Category-specific `posts.attributes` schemas. Adding a category = adding a schema here.

A schema field becomes a feed filter when it carries `json_schema_extra={"filter": "<Label>"}`.
`/meta/categories` describes these filters and `GET /posts` accepts them as query parameters
named after the field (repeat the parameter for several values; any value matches).
"""

from __future__ import annotations

import types
from dataclasses import dataclass
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel

from app.schemas.attributes.job import JobAttributes

ATTRIBUTE_SCHEMAS: dict[str, type[BaseModel]] = {
    "job": JobAttributes,
}

CATEGORY_LABELS: dict[str, str] = {
    "job": "Jobs",
    "vehicle": "Vehicles",
    "real_estate": "Real estate",
    "keyboard": "Keyboards",
    "mouse": "Mice",
}

# Labels for price columns per category; anything not listed is a "Price".
PRICE_LABELS: dict[str, str] = {"job": "Salary"}


def category_label(category: str) -> str:
    return CATEGORY_LABELS.get(category) or category.replace("_", " ").capitalize()


def validate_attributes(category: str, data: dict[str, Any]) -> dict[str, Any]:
    """Validate and clean attributes for a category. Unknown keys are dropped.

    Categories without a schema keep their attributes unchanged.
    Raises pydantic.ValidationError on invalid values.
    """
    schema = ATTRIBUTE_SCHEMAS.get(category)
    if schema is None:
        return data
    return schema.model_validate(data).model_dump(mode="json", exclude_defaults=True)


@dataclass(frozen=True)
class AttributeFilter:
    """A filterable attribute of a category, derived from its Pydantic schema."""

    key: str  # attribute name, also the GET /posts query parameter
    label: str
    is_list: bool  # the attribute holds a list (match any element) or a scalar
    value_type: Literal["string", "boolean"]
    # Allowed values when the schema fixes them (Literal); None = values come from the data.
    options: tuple[str, ...] | None


def _unwrap_optional(annotation: Any) -> Any:
    if get_origin(annotation) in (Union, types.UnionType):
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def attribute_filters(category: str) -> list[AttributeFilter]:
    schema = ATTRIBUTE_SCHEMAS.get(category)
    if schema is None:
        return []
    filters = []
    for name, field in schema.model_fields.items():
        extra = field.json_schema_extra
        label = extra.get("filter") if isinstance(extra, dict) else None
        if not label:
            continue
        annotation = _unwrap_optional(field.annotation)
        is_list = get_origin(annotation) is list
        item = get_args(annotation)[0] if is_list else annotation
        options = tuple(get_args(item)) if get_origin(item) is Literal else None
        value_type = "boolean" if item is bool else "string"
        filters.append(AttributeFilter(name, str(label), is_list, value_type, options))
    return filters
