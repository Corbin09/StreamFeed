"""FastAPI entry point. The API reads and writes the database; it never crawls."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import health, meta, posts, runs, sources, stats
from app.config import get_settings

settings = get_settings()

app = FastAPI(title="StreamFeed API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (health, posts, sources, runs, stats, meta):
    app.include_router(module.router)
