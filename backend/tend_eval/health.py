from __future__ import annotations

from typing import Any

from pymongo import MongoClient
from pymongo.errors import PyMongoError

from .catalog import build_catalog
from .config import Settings


def mongo_health(uri: str) -> dict[str, Any]:
    client: MongoClient | None = None
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=1_500, connectTimeoutMS=1_500)
        client.admin.command("ping")
        database_names = sorted(
            name for name in client.list_database_names() if name not in {"admin", "config", "local"}
        )
        collection_count = sum(len(client[name].list_collection_names()) for name in database_names)
        return {
            "available": True,
            "message": "MongoDB responded to ping.",
            "database_count": len(database_names),
            "collection_count": collection_count,
            "databases": database_names,
        }
    except PyMongoError as error:
        return {
            "available": False,
            "message": str(error),
            "database_count": 0,
            "collection_count": 0,
            "databases": [],
        }
    finally:
        if client is not None:
            client.close()


def collect_health(settings: Settings) -> dict[str, Any]:
    catalog = build_catalog(settings)
    mongodb = mongo_health(settings.mongodb_uri)
    dataset = catalog["dataset"]
    upstream = catalog["upstream"]
    ready = bool(mongodb["available"] and dataset["available"] and upstream["available"])
    return {
        "status": "ready" if ready else "degraded",
        "mongodb": mongodb,
        "dataset": dataset,
        "upstream": upstream,
        "provider": {
            "configured": settings.api_key_configured,
            "stub": settings.llm_stub,
            "ready": settings.provider_ready,
            "base_url": settings.openai_base_url,
            "model": settings.model,
            "reasoning_effort": settings.reasoning_effort,
        },
        "defaults": {"concurrency": settings.default_concurrency},
        "execution": {
            "available": bool(ready and settings.provider_ready),
            "message": (
                "Ready to execute official methods."
                if ready and settings.provider_ready
                else "Configure OPENAI_API_KEY or enable the deterministic stub."
            ),
        },
    }
