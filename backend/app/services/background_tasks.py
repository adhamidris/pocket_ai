"""Background task runner for non-blocking side-effects."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.schemas.ai_runtime import AiMessagePayload
from app.services.ai_payload_consumer import AiPayloadConsumer

logger = logging.getLogger(__name__)


def run_payload_consumer_in_background(
    business_id: UUID,
    conversation_id: UUID, 
    message_id: UUID,
    payload: AiMessagePayload,
    agent_id: UUID | None = None,
) -> None:
    """Run payload consumer in background with new database session."""
    db = SessionLocal()
    try:
        consumer = AiPayloadConsumer(db)
        result = consumer.apply(
            business_id=business_id,
            conversation_id=conversation_id,
            message_id=message_id,
            payload=payload,
            agent_id=agent_id,
        )
        logger.info(
            "Background payload processing completed",
            extra={
                "business_id": business_id,
                "conversation_id": conversation_id,
                "customer_id": result.customer_id,
                "case_id": result.case_id,
                "escalated": result.escalated,
            },
        )
    except Exception as exc:
        logger.error(
            "Background payload processing failed",
            extra={
                "business_id": business_id,
                "conversation_id": conversation_id,
                "error": str(exc),
            },
            exc_info=True,
        )
    finally:
        db.close()


class BackgroundTaskRunner:
    """Orchestrates background task execution for non-blocking operations."""
    
    def __init__(self, background_tasks: BackgroundTasks | None = None):
        self.background_tasks = background_tasks
    
    def dispatch_payload_processing(
        self,
        business_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        payload: AiMessagePayload,
        agent_id: UUID | None = None,
    ) -> None:
        """Dispatch payload processing to background task if available."""
        if self.background_tasks is not None:
            self.background_tasks.add_task(
                run_payload_consumer_in_background,
                business_id=business_id,
                conversation_id=conversation_id,
                message_id=message_id,
                payload=payload,
                agent_id=agent_id,
            )
            logger.debug("Payload processing dispatched to background task")
        else:
            # Fallback to synchronous execution
            logger.debug("Running payload processing synchronously (no background tasks)")
            run_payload_consumer_in_background(
                business_id=business_id,
                conversation_id=conversation_id,
                message_id=message_id,
                payload=payload,
                agent_id=agent_id,
            )


__all__ = ["BackgroundTaskRunner", "run_payload_consumer_in_background"]
