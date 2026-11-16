"""Category metadata that drives the frontend's tabs and filter sidebar."""

from fastapi import APIRouter
from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from app.api.deps import SessionDep, SettingsDep
from app.api.schemas import CategoriesMeta, CategoryMeta, FilterOption, FilterSpec
from app.config import Settings
from app.crawlers.registry import UnknownSourceType, load_builtin_crawlers
from app.models import Post, Source
from app.pipeline.normalize import location_label
from app.schemas.attributes import (
    ATTRIBUTE_SCHEMAS,
    PRICE_LABELS,
    AttributeFilter,
    attribute_filters,
    category_label,
)
from app.services.search import visibility_conditions

router = APIRouter(prefix="/meta", tags=["meta"])

# Free-valued attributes (skills, locations, ...) list at most this many options.
MAX_DATA_OPTIONS = 100
LANGUAGE_LABELS = {"vi": "Vietnamese", "en": "English"}


def source_type_label(source_type: str) -> str:
    try:
        label = load_builtin_crawlers().get(source_type).label
    except UnknownSourceType:
        label = None
    return label or source_type.replace("_", " ").title()


def _option_label(spec: AttributeFilter, value: str) -> str:
    if spec.key == "locations":
        return location_label(value)
    if spec.options is not None:
        return value.replace("_", " ").capitalize()
    return value


class _Facets:
    def __init__(self, session: Session, settings: Settings, category: str) -> None:
        self.session = session
        self.where = [Post.category == category, *visibility_conditions(settings)]

    def _base(self, *cols, lateral=None):
        stmt = select(*cols).select_from(Post).join(Source, Post.source_id == Source.id)
        if lateral is not None:
            stmt = stmt.join(lateral, true())
        return stmt

    def count(self) -> int:
        return self.session.scalar(self._base(func.count()).where(*self.where))

    def by(self, column, lateral=None) -> list[tuple[str, int]]:
        n = func.count().label("n")
        rows = self.session.execute(
            self._base(column.label("v"), n, lateral=lateral)
            .where(*self.where, column.is_not(None))
            .group_by("v")
            .order_by(n.desc(), "v")
            .limit(MAX_DATA_OPTIONS)
        )
        return [(str(v), c) for v, c in rows]

    def attribute(self, spec: AttributeFilter) -> list[tuple[str, int]]:
        if spec.is_list:
            elements = (
                func.jsonb_array_elements_text(Post.attributes[spec.key])
                .table_valued("value")
                .lateral()
            )
            return self.by(elements.c.value, lateral=elements)
        return self.by(Post.attributes[spec.key].astext)


def _filters(facets: _Facets, category: str) -> list[FilterSpec]:
    by_type = facets.by(Source.type)
    by_language = dict(facets.by(Post.language))
    filters = [
        FilterSpec(
            key="source_type",
            label="Source",
            type="multi_select",
            options=[
                FilterOption(value=t, label=source_type_label(t), count=n) for t, n in by_type
            ],
        ),
        FilterSpec(
            key="language",
            label="Language",
            type="select",
            options=[
                FilterOption(value=lang, label=label, count=by_language.get(lang, 0))
                for lang, label in LANGUAGE_LABELS.items()
            ],
        ),
    ]
    for spec in attribute_filters(category):
        if spec.value_type == "boolean":
            filters.append(FilterSpec(key=spec.key, label=spec.label, type="boolean", options=[]))
            continue
        counts = facets.attribute(spec)
        if spec.options is not None:
            found = dict(counts)
            values = [(v, found.get(v, 0)) for v in spec.options]
        else:
            values = counts
        filters.append(
            FilterSpec(
                key=spec.key,
                label=spec.label,
                type="multi_select",
                options=[
                    FilterOption(value=v, label=_option_label(spec, v), count=n) for v, n in values
                ],
            )
        )
    return filters


@router.get("/categories", response_model=CategoriesMeta)
def categories(session: SessionDep, settings: SettingsDep) -> CategoriesMeta:
    """Categories that have a schema or posts, each with its filter schema and option counts.

    Every filter `key` is a `GET /posts` query parameter. Counts cover the default feed.
    """
    with_posts = session.scalars(select(Post.category).distinct()).all()
    keys = list(ATTRIBUTE_SCHEMAS) + sorted(set(with_posts) - set(ATTRIBUTE_SCHEMAS))
    out = []
    for key in keys:
        facets = _Facets(session, settings, key)
        out.append(
            CategoryMeta(
                key=key,
                label=category_label(key),
                price_label=PRICE_LABELS.get(key, "Price"),
                post_count=facets.count(),
                filters=_filters(facets, key),
            )
        )
    return CategoriesMeta(categories=out, usd_vnd_rate=settings.usd_vnd_rate)
