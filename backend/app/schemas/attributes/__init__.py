"""Category-specific `posts.attributes` schemas. Adding a category = adding a schema here."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.schemas.attributes.job import JobAttributes

ATTRIBUTE_SCHEMAS: dict[str, type[BaseModel]] = {
    "job": JobAttributes,
}


def validate_attributes(category: str, data: dict[str, Any]) -> dict[str, Any]:
    """Validate and clean attributes for a category. Unknown keys are dropped.

    Categories without a schema keep their attributes unchanged.
    Raises pydantic.ValidationError on invalid values.
    """
    schema = ATTRIBUTE_SCHEMAS.get(category)
    if schema is None:
        return data
    return schema.model_validate(data).model_dump(mode="json", exclude_defaults=True)
