"""Repository implementations for the registration flow."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.models.registration import (
    Agent,
    AgentRole,
    AgentTone,
    AgentTrait,
    AgentTraitLink,
    EscalationRule,
    Business,
    BusinessNiche,
    Industry,
    IndustryNiche,
    KnowledgeItem,
    KnowledgeItemFile,
    KnowledgeItemText,
    KnowledgeItemUrl,
    KnowledgeSource,
    KnowledgeStatus,
    MembershipRole,
    RegistrationSession,
    RegistrationStep,
    User,
    UserBusinessMembership,
)
from app.repositories.base import BaseRepository, utc_now
from app.repositories.dtos import (
    AgentRecord,
    AgentTraitRecord,
    AgentWithTraitsRecord,
    BusinessNicheRecord,
    BusinessRecord,
    KnowledgeItemFileRecord,
    KnowledgeItemRecord,
    KnowledgeItemTextRecord,
    KnowledgeItemUrlRecord,
    KnowledgeItemWithDetailRecord,
    MembershipRecord,
    RegistrationSessionRecord,
    UserRecord,
)
from app.repositories.errors import ConflictError, ExpiredError, NotFoundError, ValidationError


def _to_registration_session(model: RegistrationSession) -> RegistrationSessionRecord:
    return RegistrationSessionRecord(
        id=model.id,
        user_id=model.user_id,
        business_id=model.business_id,
        current_step=model.current_step,
        state=model.state,
        expires_at=model.expires_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
        steps_completed=model.steps_completed,
        total_steps=model.total_steps,
    )


def _to_user(model: User) -> UserRecord:
    return UserRecord(
        id=model.id,
        email=model.email,
        first_name=model.first_name,
        password_hash=model.password_hash,
        auth_provider=model.auth_provider,
        email_verified=model.email_verified,
        created_at=model.created_at,
    )


def _to_business(model: Business) -> BusinessRecord:
    return BusinessRecord(
        id=model.id,
        name=model.name,
        industry_code=model.industry_code,
        created_by_user_id=model.created_by_user_id,
        created_by_user_name=model.created_by_user_name,
        created_at=model.created_at,
    )


def _to_business_niche(model: BusinessNiche) -> BusinessNicheRecord:
    return BusinessNicheRecord(
        business_id=model.business_id,
        niche_code=model.niche_code,
        added_at=model.added_at,
    )


def _to_membership(model: UserBusinessMembership) -> MembershipRecord:
    return MembershipRecord(
        user_id=model.user_id,
        business_id=model.business_id,
        role=model.role,
        joined_at=model.joined_at,
    )


def _fallback_label_from_code(code: str, *, prefix: str, default: str) -> str:
    if not code.lower().startswith(prefix):
        return default
    raw = code[len(prefix) :]
    pieces = [segment for segment in raw.split("-") if segment]
    if not pieces:
        return default
    return " ".join(piece.capitalize() for piece in pieces)


class IndustryCatalogRepository(BaseRepository):
    """Ensure industry and niche catalogue entries exist for registration."""

    def ensure_industry(self, *, code: str, label: str) -> None:
        label_clean = label.strip() or _fallback_label_from_code(
            code, prefix="industry:", default="Industry"
        )
        with self._with_timeout():
            model = self.session.get(Industry, code)
            if model is None:
                self.session.add(
                    Industry(
                        code=code,
                        label=label_clean,
                    )
                )
            elif label_clean and model.label != label_clean:
                model.label = label_clean
            self.session.flush()

    def ensure_niches(self, *, industry_code: str, items: Mapping[str, str]) -> None:
        if not items:
            return
        with self._with_timeout():
            stmt = select(IndustryNiche).where(IndustryNiche.code.in_(list(items.keys())))
            existing = {model.code: model for model in self.session.scalars(stmt).all()}

            for code, label in items.items():
                label_clean = label.strip() or _fallback_label_from_code(
                    code, prefix="niche:", default="Niche"
                )
                model = existing.get(code)
                if model is None:
                    self.session.add(
                        IndustryNiche(
                            code=code,
                            industry_code=industry_code,
                            label=label_clean,
                        )
                    )
                else:
                    if model.industry_code != industry_code:
                        model.industry_code = industry_code
                    if label_clean and model.label != label_clean:
                        model.label = label_clean
            self.session.flush()


def _to_agent(model: Agent) -> AgentRecord:
    return AgentRecord(
        id=model.id,
        business_id=model.business_id,
        name=model.name,
        role=model.role,
        tone=model.tone,
        escalation_rule=model.escalation_rule,
        status=model.status,
        public_slug=model.public_slug,
        avatar_url=model.avatar_url,
        created_by_user_id=model.created_by_user_id,
        created_by_user_name=model.created_by_user_name,
        created_at=model.created_at,
    )


def _to_agent_trait(model: AgentTraitLink) -> AgentTraitRecord:
    return AgentTraitRecord(
        agent_id=model.agent_id,
        trait_code=model.trait_code,
        added_at=model.added_at,
    )


def _to_knowledge_item(model: KnowledgeItem) -> KnowledgeItemRecord:
    return KnowledgeItemRecord(
        id=model.id,
        business_id=model.business_id,
        source_type=model.source_type,
        status=model.status,
        display_name=model.display_name,
        language=model.language,
        created_by_user_id=model.created_by_user_id,
        created_by_user_name=model.created_by_user_name,
        created_at=model.created_at,
    )


def _to_knowledge_file(model: KnowledgeItemFile | None) -> KnowledgeItemFileRecord | None:
    if model is None:
        return None
    return KnowledgeItemFileRecord(
        knowledge_item_id=model.knowledge_item_id,
        filename=model.filename,
        content_type=model.content_type,
        storage_path=model.storage_path,
        size_bytes=model.size_bytes,
        checksum_sha256=model.checksum_sha256,
    )


def _to_knowledge_url(model: KnowledgeItemUrl | None) -> KnowledgeItemUrlRecord | None:
    if model is None:
        return None
    return KnowledgeItemUrlRecord(
        knowledge_item_id=model.knowledge_item_id,
        url=model.url,
    )


def _to_knowledge_text(model: KnowledgeItemText | None) -> KnowledgeItemTextRecord | None:
    if model is None:
        return None
    return KnowledgeItemTextRecord(
        knowledge_item_id=model.knowledge_item_id,
        text_content=model.text_content,
    )


class RegistrationSessionsRepository(BaseRepository):
    """Access patterns for registration session persistence."""

    def create_session(
        self,
        *,
        user_id: uuid.UUID,
        initial_step: RegistrationStep = RegistrationStep.BUSINESS_PROFILE,
        ttl_days: int = 7,
    ) -> RegistrationSessionRecord:
        with self._with_timeout():
            expires_at = utc_now() + timedelta(days=ttl_days)
            session_model = RegistrationSession(
                user_id=user_id,
                current_step=initial_step,
                expires_at=expires_at,
            )
            self.session.add(session_model)
            self.session.flush()
            return _to_registration_session(session_model)

    def _load_session_for_user(
        self, registration_id: uuid.UUID, for_user_id: uuid.UUID
    ) -> RegistrationSession:
        stmt = select(RegistrationSession).where(
            RegistrationSession.id == registration_id,
            RegistrationSession.user_id == for_user_id,
        )
        model = self.session.scalars(stmt).first()
        if model is None:
            raise NotFoundError(
                "Registration session not found",
                details={"registration_id": str(registration_id)},
            )
        return model

    def get_session(
        self, *, registration_id: uuid.UUID, for_user_id: uuid.UUID
    ) -> RegistrationSessionRecord:
        with self._with_timeout():
            model = self._load_session_for_user(registration_id, for_user_id)
            if model.expires_at <= utc_now():
                raise ExpiredError(
                    "Registration session expired",
                    details={"registration_id": str(registration_id)},
                )
            return _to_registration_session(model)

    def attach_business(
        self, *, registration_id: uuid.UUID, for_user_id: uuid.UUID, business_id: uuid.UUID
    ) -> RegistrationSessionRecord:
        with self._with_timeout():
            model = self._load_session_for_user(registration_id, for_user_id)
            if model.business_id is None:
                model.business_id = business_id
            elif model.business_id != business_id:
                raise ConflictError(
                    "Registration session already linked to a different business",
                    details={
                        "registration_id": str(registration_id),
                        "existing_business_id": str(model.business_id),
                    },
                )
            self.session.flush()
            return _to_registration_session(model)

    def update_progress(
        self,
        *,
        registration_id: uuid.UUID,
        for_user_id: uuid.UUID,
        current_step: RegistrationStep,
        state_patch: dict[str, Any] | None,
        expected_updated_at: datetime | None = None,
        steps_completed: int | None = None,
    ) -> RegistrationSessionRecord:
        with self._with_timeout():
            model = self._load_session_for_user(registration_id, for_user_id)
            if expected_updated_at and model.updated_at != expected_updated_at:
                raise ConflictError(
                    "Registration session update conflict",
                    details={
                        "registration_id": str(registration_id),
                        "expected": expected_updated_at.isoformat(),
                        "actual": model.updated_at.isoformat(),
                    },
                )
            model.current_step = current_step
            if state_patch is not None:
                model.state = state_patch
            if steps_completed is not None:
                if steps_completed < 0 or steps_completed > model.total_steps:
                    raise ValidationError(
                        "steps_completed outside allowed range",
                        details={
                            "steps_completed": steps_completed,
                            "total_steps": model.total_steps,
                        },
                    )
                model.steps_completed = steps_completed
            self.session.flush()
            return _to_registration_session(model)

    def mark_completed(
        self,
        *,
        registration_id: uuid.UUID,
        for_user_id: uuid.UUID,
    ) -> RegistrationSessionRecord:
        with self._with_timeout():
            model = self._load_session_for_user(registration_id, for_user_id)
            model.current_step = RegistrationStep.COMPLETED
            model.steps_completed = model.total_steps
            self.session.flush()
            return _to_registration_session(model)

    def list_sessions(
        self,
        *,
        for_user_id: uuid.UUID,
        status: str = "active",
        limit: int = 20,
        offset: int = 0,
        sort_by: str = "updated_at",
        order: str = "desc",
    ) -> list[RegistrationSessionRecord]:
        with self._with_timeout():
            if limit < 1 or limit > 100:
                raise ValidationError(
                    "limit must be between 1 and 100",
                    details={"limit": limit},
                )
            if offset < 0 or offset > 10_000:
                raise ValidationError(
                    "offset must be between 0 and 10000",
                    details={"offset": offset},
                )

            stmt: Select[tuple[RegistrationSession]] = select(RegistrationSession).where(
                RegistrationSession.user_id == for_user_id
            )

            now = utc_now()
            if status == "active":
                stmt = stmt.where(RegistrationSession.expires_at > now)
            elif status == "expired":
                stmt = stmt.where(RegistrationSession.expires_at <= now)
            elif status != "all":
                raise ValidationError(
                    "status must be one of ['active','expired','all']",
                    details={"status": status},
                )

            order_by_column = RegistrationSession.updated_at
            if sort_by == "created_at":
                order_by_column = RegistrationSession.created_at
            elif sort_by not in {"updated_at", "created_at"}:
                raise ValidationError(
                    "Unsupported sort_by value",
                    details={"sort_by": sort_by},
                )

            if order == "asc":
                stmt = stmt.order_by(order_by_column.asc())
            elif order == "desc":
                stmt = stmt.order_by(order_by_column.desc())
            else:
                raise ValidationError(
                    "order must be 'asc' or 'desc'",
                    details={"order": order},
                )

            stmt = stmt.offset(offset).limit(limit)
            models = self.session.scalars(stmt).all()
            return [_to_registration_session(model) for model in models]

    def expire_sessions(self, *, before: datetime) -> int:
        with self._with_timeout():
            result = self.session.execute(
                update(RegistrationSession)
                .where(RegistrationSession.expires_at <= before)
                .values(expires_at=before)
            )
            return int(result.rowcount or 0)

    def compute_step_completion(
        self, *, registration_id: uuid.UUID, for_user_id: uuid.UUID
    ) -> dict[str, Any]:
        with self._with_timeout():
            model = self._load_session_for_user(registration_id, for_user_id)
            business_id = model.business_id
            if business_id is None:
                return {
                    "business_created": False,
                    "agent_configured": False,
                    "knowledge_count": 0,
                }

            business_exists = self.session.scalar(
                select(func.count())
                .select_from(Business)
                .where(Business.id == business_id)
            )
            agent_exists = self.session.scalar(
                select(func.count())
                .select_from(Agent)
                .where(Agent.business_id == business_id)
            )
            knowledge_count = self.session.scalar(
                select(func.count())
                .select_from(KnowledgeItem)
                .where(KnowledgeItem.business_id == business_id)
            )

            return {
                "business_created": bool(business_exists),
                "agent_configured": bool(agent_exists),
                "knowledge_count": int(knowledge_count or 0),
            }


class UsersRepository(BaseRepository):
    """User persistence helpers for the registration flow."""

    def find_by_email(self, *, email_lower: str) -> UserRecord | None:
        with self._with_timeout():
            stmt = select(User).where(func.lower(User.email) == email_lower)
            model = self.session.scalars(stmt).first()
            return _to_user(model) if model else None

    def create_user(
        self,
        *,
        first_name: str,
        email: str,
        password_hash: str | None,
        auth_provider: str,
        email_verified: bool = False,
    ) -> UserRecord:
        with self._with_timeout():
            user = User(
                first_name=first_name,
                email=email,
                password_hash=password_hash,
                auth_provider=auth_provider,
                email_verified=email_verified,
            )
            self.session.add(user)
            try:
                self.session.flush()
            except IntegrityError as exc:
                raise ConflictError(
                    "User with the provided email already exists",
                    details={"email": email},
                ) from exc
            return _to_user(user)

    def mark_email_verified(self, *, user_id: uuid.UUID) -> None:
        with self._with_timeout():
            stmt = (
                update(User)
                .where(User.id == user_id)
                .values(email_verified=True)
            )
            result = self.session.execute(stmt)
            if result.rowcount == 0:
                raise NotFoundError(
                    "User not found",
                    details={"user_id": str(user_id)},
                )


class BusinessesRepository(BaseRepository):
    """Business persistence for registration."""

    def create_business(
        self,
        *,
        name: str,
        industry_code: str,
        created_by_user_id: uuid.UUID,
        created_by_user_name: str,
    ) -> BusinessRecord:
        with self._with_timeout():
            business = Business(
                name=name,
                industry_code=industry_code,
                created_by_user_id=created_by_user_id,
                created_by_user_name=created_by_user_name,
            )
            self.session.add(business)
            self.session.flush()
            return _to_business(business)

    def get_business(self, *, business_id: uuid.UUID) -> BusinessRecord:
        with self._with_timeout():
            model = self.session.get(Business, business_id)
            if model is None:
                raise NotFoundError(
                    "Business not found",
                    details={"business_id": str(business_id)},
                )
            return _to_business(model)

    def list_by_creator(
        self,
        *,
        user_id: uuid.UUID,
        limit: int = 20,
        offset: int = 0,
    ) -> list[BusinessRecord]:
        with self._with_timeout():
            if limit < 1 or limit > 100:
                raise ValidationError("limit must be between 1 and 100", details={"limit": limit})
            if offset < 0 or offset > 10_000:
                raise ValidationError(
                    "offset must be between 0 and 10000",
                    details={"offset": offset},
                )

            stmt = (
                select(Business)
                .where(Business.created_by_user_id == user_id)
                .order_by(Business.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            models = self.session.scalars(stmt).all()
            return [_to_business(model) for model in models]


class BusinessNichesRepository(BaseRepository):
    """Manage business niche selections."""

    def replace_niches(
        self, *, business_id: uuid.UUID, niche_codes: Sequence[str]
    ) -> list[BusinessNicheRecord]:
        with self._with_timeout():
            existing_stmt = select(BusinessNiche).where(BusinessNiche.business_id == business_id)
            existing_models = self.session.scalars(existing_stmt).all()
            existing_codes = {model.niche_code for model in existing_models}

            desired_codes = {code for code in niche_codes}

            to_remove = existing_codes - desired_codes
            to_add = desired_codes - existing_codes

            if to_remove:
                self.session.execute(
                    delete(BusinessNiche)
                    .where(BusinessNiche.business_id == business_id)
                    .where(BusinessNiche.niche_code.in_(list(to_remove)))
                )

            for code in to_add:
                self.session.add(BusinessNiche(business_id=business_id, niche_code=code))

            self.session.flush()

            refreshed_stmt = select(BusinessNiche).where(BusinessNiche.business_id == business_id)
            refreshed = self.session.scalars(refreshed_stmt).all()
            return [_to_business_niche(model) for model in refreshed]

    def list_niches(self, *, business_id: uuid.UUID) -> list[BusinessNicheRecord]:
        with self._with_timeout():
            stmt = (
                select(BusinessNiche)
                .where(BusinessNiche.business_id == business_id)
                .order_by(BusinessNiche.added_at.asc())
            )
            models = self.session.scalars(stmt).all()
            return [_to_business_niche(model) for model in models]


class MembershipsRepository(BaseRepository):
    """Manage user-to-business membership records."""

    def add_owner(self, *, user_id: uuid.UUID, business_id: uuid.UUID) -> MembershipRecord:
        with self._with_timeout():
            membership = UserBusinessMembership(
                user_id=user_id,
                business_id=business_id,
                role=MembershipRole.OWNER,
            )
            self.session.add(membership)
            try:
                self.session.flush()
            except IntegrityError:
                stmt = select(UserBusinessMembership).where(
                    UserBusinessMembership.user_id == user_id,
                    UserBusinessMembership.business_id == business_id,
                )
                existing = self.session.scalars(stmt).first()
                if existing is None:
                    raise ConflictError(
                        "Membership insert failed",
                        details={
                            "user_id": str(user_id),
                            "business_id": str(business_id),
                        },
                    )
                return _to_membership(existing)
            return _to_membership(membership)

    def get_roles(self, *, business_id: uuid.UUID, user_id: uuid.UUID) -> list[MembershipRole]:
        with self._with_timeout():
            stmt = select(UserBusinessMembership.role).where(
                UserBusinessMembership.business_id == business_id,
                UserBusinessMembership.user_id == user_id,
            )
            return list(self.session.scalars(stmt).all())

    def list_members(
        self,
        *,
        business_id: uuid.UUID,
        role: MembershipRole | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[MembershipRecord]:
        with self._with_timeout():
            if limit < 1 or limit > 100:
                raise ValidationError("limit must be between 1 and 100", details={"limit": limit})
            if offset < 0 or offset > 10_000:
                raise ValidationError(
                    "offset must be between 0 and 10000",
                    details={"offset": offset},
                )

            stmt = select(UserBusinessMembership).where(
                UserBusinessMembership.business_id == business_id
            )
            if role is not None:
                stmt = stmt.where(UserBusinessMembership.role == role)

            stmt = stmt.order_by(UserBusinessMembership.joined_at.desc())
            stmt = stmt.offset(offset).limit(limit)
            models = self.session.scalars(stmt).all()
            return [_to_membership(model) for model in models]


class AgentsRepository(BaseRepository):
    """Agent configuration persistence."""

    def _load_agent(self, *, business_id: uuid.UUID, agent_id: uuid.UUID) -> Agent:
        stmt = select(Agent).where(
            Agent.id == agent_id,
            Agent.business_id == business_id,
        )
        model = self.session.scalars(stmt).first()
        if model is None:
            raise NotFoundError(
                "Agent not found",
                details={
                    "agent_id": str(agent_id),
                    "business_id": str(business_id),
                },
            )
        return model

    def create_agent(
        self,
        *,
        business_id: uuid.UUID,
        name: str,
        role: AgentRole,
        tone: AgentTone,
        escalation_rule: EscalationRule,
        created_by_user_id: uuid.UUID,
        created_by_user_name: str,
    ) -> AgentRecord:
        with self._with_timeout():
            # --- Generate canonical slug from name; ensure per-business uniqueness ---
            import re
            def _slugify(value: str) -> str:
                slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
                slug = re.sub(r"-+", "-", slug)[:120]
                return slug
            base = _slugify(name) or "agent"
            candidate = base
            suffix = 2
            while True:
                exists = self.session.execute(
                    select(Agent.id).where(
                        Agent.business_id == business_id,
                        func.lower(Agent.public_slug) == candidate.lower(),
                    ).limit(1)
                ).first()
                if not exists:
                    break
                candidate = f"{base}-{suffix}"
                suffix += 1
                if len(candidate) > 120:
                    candidate = candidate[:120]
            agent = Agent(
                business_id=business_id,
                name=name,
                role=role,
                tone=tone,
                escalation_rule=escalation_rule,
                created_by_user_id=created_by_user_id,
                created_by_user_name=created_by_user_name,
                public_slug=candidate,
            )
            self.session.add(agent)
            self.session.flush()
            return _to_agent(agent)

    def get_agent(self, *, business_id: uuid.UUID, agent_id: uuid.UUID) -> AgentRecord:
        with self._with_timeout():
            model = self._load_agent(business_id=business_id, agent_id=agent_id)
            return _to_agent(model)

    def get_agent_with_traits(
        self, *, business_id: uuid.UUID, agent_id: uuid.UUID
    ) -> AgentWithTraitsRecord:
        with self._with_timeout():
            stmt = (
                select(Agent)
                .options(selectinload(Agent.traits))
                .where(Agent.id == agent_id, Agent.business_id == business_id)
            )
            model = self.session.scalars(stmt).first()
            if model is None:
                raise NotFoundError(
                    "Agent not found",
                    details={
                        "agent_id": str(agent_id),
                        "business_id": str(business_id),
                    },
                )
            agent_record = _to_agent(model)
            traits = [_to_agent_trait(trait) for trait in model.traits]
            return AgentWithTraitsRecord(agent=agent_record, traits=traits)

    def list_agents(
        self,
        *,
        business_id: uuid.UUID,
        q_name: str | None = None,
        role: AgentRole | None = None,
        limit: int = 20,
        offset: int = 0,
        sort_by: str = "created_at",
        order: str = "desc",
    ) -> list[AgentRecord]:
        with self._with_timeout():
            if limit < 1 or limit > 100:
                raise ValidationError("limit must be between 1 and 100", details={"limit": limit})
            if offset < 0 or offset > 10_000:
                raise ValidationError(
                    "offset must be between 0 and 10000",
                    details={"offset": offset},
                )

            stmt = select(Agent).where(Agent.business_id == business_id)
            if q_name:
                stmt = stmt.where(func.lower(Agent.name).like(f"%{q_name.lower()}%"))
            if role:
                stmt = stmt.where(Agent.role == role)

            order_column = Agent.created_at
            if sort_by == "name":
                order_column = Agent.name
            elif sort_by != "created_at":
                raise ValidationError(
                    "Unsupported sort_by value",
                    details={"sort_by": sort_by},
                )

            if order == "asc":
                stmt = stmt.order_by(order_column.asc())
            elif order == "desc":
                stmt = stmt.order_by(order_column.desc())
            else:
                raise ValidationError(
                    "order must be 'asc' or 'desc'",
                    details={"order": order},
                )

            stmt = stmt.offset(offset).limit(limit)
            models = self.session.scalars(stmt).all()
            return [_to_agent(model) for model in models]

    def update_agent(
        self,
        *,
        business_id: uuid.UUID,
        agent_id: uuid.UUID,
        patch: dict[str, Any],
        expected_updated_at: datetime | None = None,
    ) -> AgentRecord:
        with self._with_timeout():
            model = self._load_agent(business_id=business_id, agent_id=agent_id)
            model_updated_at = getattr(model, "updated_at", None)
            if expected_updated_at is not None and model_updated_at is not None:
                if model_updated_at != expected_updated_at:
                    raise ConflictError(
                        "Agent update conflict",
                        details={
                            "agent_id": str(agent_id),
                            "expected": expected_updated_at.isoformat(),
                            "actual": model_updated_at.isoformat(),
                        },
                    )
            elif expected_updated_at is not None and model_updated_at is None:
                raise ConflictError("Agent does not support optimistic locking", details={"agent_id": str(agent_id)})
            # --- Apply non-handle fields first (name changes DO NOT change public_slug) ---
            if "name" in patch:
                model.name = patch["name"]
            if "role" in patch:
                model.role = patch["role"]
            if "tone" in patch:
                model.tone = patch["tone"]
            if "escalation_rule" in patch:
                model.escalation_rule = patch["escalation_rule"]
            # --- Explicit public_slug updates (slugify + enforce per-business uniqueness) ---
            if "public_slug" in patch and patch["public_slug"] is not None:
                import re
                def _slugify(value: str) -> str:
                    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
                    slug = re.sub(r"-+", "-", slug)[:120]
                    return slug
                base = _slugify(patch["public_slug"])
                if not base or len(base) < 3:
                    raise ValidationError("public_slug must be at least 3 characters after slugify", details={"public_slug": patch["public_slug"]})
                candidate = base
                suffix = 2
                while True:
                    exists = self.session.execute(
                        select(Agent.id).where(
                            Agent.business_id == business_id,
                            func.lower(Agent.public_slug) == candidate.lower(),
                            Agent.id != agent_id,
                        ).limit(1)
                    ).first()
                    if not exists:
                        break
                    candidate = f"{base}-{suffix}"
                    suffix += 1
                    if len(candidate) > 120:
                        candidate = candidate[:120]
                model.public_slug = candidate
            self.session.flush()
            return _to_agent(model)

    def replace_traits(
        self,
        *,
        business_id: uuid.UUID,
        agent_id: uuid.UUID,
        trait_codes: Iterable[AgentTrait | str],
    ) -> list[AgentTraitRecord]:
        with self._with_timeout():
            agent = self._load_agent(business_id=business_id, agent_id=agent_id)
            desired: set[AgentTrait] = set()
            for code in trait_codes:
                if isinstance(code, AgentTrait):
                    desired.add(code)
                else:
                    try:
                        desired.add(AgentTrait(code))
                    except ValueError as exc:
                        raise ValidationError(
                            "Unsupported agent trait",
                            details={"trait_code": code},
                        ) from exc

            existing_stmt = select(AgentTraitLink).where(AgentTraitLink.agent_id == agent.id)
            existing_models = self.session.scalars(existing_stmt).all()
            existing_codes = {model.trait_code for model in existing_models}

            to_remove = existing_codes - desired
            to_add = desired - existing_codes

            if to_remove:
                self.session.execute(
                    delete(AgentTraitLink)
                    .where(AgentTraitLink.agent_id == agent.id)
                    .where(AgentTraitLink.trait_code.in_(list(to_remove)))
                )

            for code in to_add:
                self.session.add(AgentTraitLink(agent_id=agent.id, trait_code=code))

            self.session.flush()

            refreshed_stmt = select(AgentTraitLink).where(AgentTraitLink.agent_id == agent.id)
            refreshed = self.session.scalars(refreshed_stmt).all()
            return [_to_agent_trait(model) for model in refreshed]


class KnowledgeItemsRepository(BaseRepository):
    """Persistence utilities for knowledge attachments."""

    def _base_select(self, *, business_id: uuid.UUID) -> Select[tuple[KnowledgeItem]]:
        return select(KnowledgeItem).where(KnowledgeItem.business_id == business_id)

    def create_url(
        self,
        *,
        business_id: uuid.UUID,
        display_name: str | None,
        url: str,
        language: str | None,
        created_by_user_id: uuid.UUID,
        created_by_user_name: str,
    ) -> KnowledgeItemWithDetailRecord:
        with self._with_timeout():
            stmt = (
                select(KnowledgeItem)
                .join(KnowledgeItemUrl)
                .options(joinedload(KnowledgeItem.url))
                .where(
                    KnowledgeItem.business_id == business_id,
                    KnowledgeItem.source_type == KnowledgeSource.URL,
                    KnowledgeItemUrl.url == url,
                )
            )
            existing = self.session.scalars(stmt).first()
            if existing:
                return KnowledgeItemWithDetailRecord(
                    item=_to_knowledge_item(existing),
                    file=_to_knowledge_file(existing.file),
                    url=_to_knowledge_url(existing.url),
                    text=_to_knowledge_text(existing.text_content),
                )

            item = KnowledgeItem(
                business_id=business_id,
                source_type=KnowledgeSource.URL,
                display_name=display_name,
                language=language,
                created_by_user_id=created_by_user_id,
                created_by_user_name=created_by_user_name,
            )
            item.url = KnowledgeItemUrl(url=url)
            self.session.add(item)
            self.session.flush()
            return KnowledgeItemWithDetailRecord(
                item=_to_knowledge_item(item),
                file=_to_knowledge_file(item.file),
                url=_to_knowledge_url(item.url),
                text=_to_knowledge_text(item.text_content),
            )

    def create_file(
        self,
        *,
        business_id: uuid.UUID,
        display_name: str | None,
        storage_path: str,
        filename: str,
        content_type: str | None,
        size_bytes: int,
        checksum_sha256: str | None,
        language: str | None,
        created_by_user_id: uuid.UUID,
        created_by_user_name: str,
    ) -> KnowledgeItemWithDetailRecord:
        with self._with_timeout():
            item = KnowledgeItem(
                business_id=business_id,
                source_type=KnowledgeSource.FILE,
                display_name=display_name,
                language=language,
                created_by_user_id=created_by_user_id,
                created_by_user_name=created_by_user_name,
            )
            item.file = KnowledgeItemFile(
                storage_path=storage_path,
                filename=filename,
                content_type=content_type,
                size_bytes=size_bytes,
                checksum_sha256=checksum_sha256,
            )
            self.session.add(item)
            self.session.flush()
            return KnowledgeItemWithDetailRecord(
                item=_to_knowledge_item(item),
                file=_to_knowledge_file(item.file),
                url=None,
                text=None,
            )

    def create_text(
        self,
        *,
        business_id: uuid.UUID,
        display_name: str | None,
        text_content: str,
        language: str | None,
        created_by_user_id: uuid.UUID,
        created_by_user_name: str,
    ) -> KnowledgeItemWithDetailRecord:
        with self._with_timeout():
            item = KnowledgeItem(
                business_id=business_id,
                source_type=KnowledgeSource.TEXT,
                display_name=display_name,
                language=language,
                created_by_user_id=created_by_user_id,
                created_by_user_name=created_by_user_name,
            )
            item.text_content = KnowledgeItemText(text_content=text_content)
            self.session.add(item)
            self.session.flush()
            return KnowledgeItemWithDetailRecord(
                item=_to_knowledge_item(item),
                file=None,
                url=None,
                text=_to_knowledge_text(item.text_content),
            )

    def list_items(
        self,
        *,
        business_id: uuid.UUID,
        status: Sequence[KnowledgeStatus] | None = None,
        source_type: Sequence[KnowledgeSource] | None = None,
        q_name: str | None = None,
        limit: int = 20,
        offset: int = 0,
        sort_by: str = "created_at",
        order: str = "desc",
    ) -> list[KnowledgeItemRecord]:
        with self._with_timeout():
            if limit < 1 or limit > 100:
                raise ValidationError("limit must be between 1 and 100", details={"limit": limit})
            if offset < 0 or offset > 10_000:
                raise ValidationError(
                    "offset must be between 0 and 10000",
                    details={"offset": offset},
                )

            stmt = self._base_select(business_id=business_id)
            if status:
                stmt = stmt.where(KnowledgeItem.status.in_(list(status)))
            if source_type:
                stmt = stmt.where(KnowledgeItem.source_type.in_(list(source_type)))
            if q_name:
                stmt = stmt.where(func.lower(KnowledgeItem.display_name).like(f"%{q_name.lower()}%"))

            order_column = KnowledgeItem.created_at
            if sort_by == "status":
                order_column = KnowledgeItem.status
            elif sort_by not in {"created_at", "status"}:
                raise ValidationError(
                    "Unsupported sort_by value",
                    details={"sort_by": sort_by},
                )

            if order == "asc":
                stmt = stmt.order_by(order_column.asc())
            elif order == "desc":
                stmt = stmt.order_by(order_column.desc())
            else:
                raise ValidationError(
                    "order must be 'asc' or 'desc'",
                    details={"order": order},
                )

            stmt = stmt.offset(offset).limit(limit)
            models = self.session.scalars(stmt).all()
            return [_to_knowledge_item(model) for model in models]

    def get_item_with_detail(
        self, *, business_id: uuid.UUID, knowledge_item_id: uuid.UUID
    ) -> KnowledgeItemWithDetailRecord:
        with self._with_timeout():
            stmt = (
                select(KnowledgeItem)
                .options(
                    joinedload(KnowledgeItem.file),
                    joinedload(KnowledgeItem.url),
                    joinedload(KnowledgeItem.text_content),
                )
                .where(
                    KnowledgeItem.id == knowledge_item_id,
                    KnowledgeItem.business_id == business_id,
                )
            )
            model = self.session.scalars(stmt).first()
            if model is None:
                raise NotFoundError(
                    "Knowledge item not found",
                    details={
                        "knowledge_item_id": str(knowledge_item_id),
                        "business_id": str(business_id),
                    },
                )
            return KnowledgeItemWithDetailRecord(
                item=_to_knowledge_item(model),
                file=_to_knowledge_file(model.file),
                url=_to_knowledge_url(model.url),
                text=_to_knowledge_text(model.text_content),
            )

    def update_status(
        self,
        *,
        business_id: uuid.UUID,
        knowledge_item_id: uuid.UUID,
        status: KnowledgeStatus,
    ) -> None:
        with self._with_timeout():
            stmt = (
                update(KnowledgeItem)
                .where(
                    KnowledgeItem.id == knowledge_item_id,
                    KnowledgeItem.business_id == business_id,
                )
                .values(status=status)
            )
            result = self.session.execute(stmt)
            if result.rowcount == 0:
                raise NotFoundError(
                    "Knowledge item not found",
                    details={
                        "knowledge_item_id": str(knowledge_item_id),
                        "business_id": str(business_id),
                    },
                )


__all__ = [
    "AgentsRepository",
    "BusinessNichesRepository",
    "BusinessesRepository",
    "KnowledgeItemsRepository",
    "MembershipsRepository",
    "RegistrationSessionsRepository",
    "UsersRepository",
]
