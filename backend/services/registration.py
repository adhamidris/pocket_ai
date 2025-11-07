"""Service orchestration for the web registration wizard."""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Mapping, MutableMapping, Protocol, Sequence

from sqlalchemy.orm import Session

from app.models.registration import (
    AgentRole,
    AgentTone,
    AgentTrait,
    EscalationRule,
    KnowledgeSource,
    MembershipRole,
    RegistrationStep,
)
from app.repositories.dtos import (
    AgentWithTraitsRecord,
    BusinessNicheRecord,
    BusinessRecord,
    KnowledgeItemWithDetailRecord,
    RegistrationSessionRecord,
    UserRecord,
)
from app.repositories.errors import (
    ConflictError as RepositoryConflictError,
    DbTimeoutError as RepositoryTimeoutError,
    ExpiredError as RepositoryExpiredError,
    NotFoundError as RepositoryNotFoundError,
    RepositoryError,
    ValidationError as RepositoryValidationError,
)
from app.repositories.base import utc_now
from app.repositories.registration import (
    AgentsRepository,
    BusinessNichesRepository,
    BusinessesRepository,
    IndustryCatalogRepository,
    KnowledgeItemsRepository,
    MembershipsRepository,
    RegistrationSessionsRepository,
    UsersRepository,
)
from app.services.errors import (
    ServiceConflictError,
    ServiceError,
    ServiceExpiredError,
    ServiceNotFoundError,
    ServiceTimeoutError,
    ServiceValidationError,
)


class RegistrationCatalogMapper(Protocol):
    """Maps UI-facing labels into internal codes/enums."""

    def industry_to_code(self, *, label: str, custom: str | None = None) -> str:
        ...

    def line_of_business_to_codes(
        self, *, industry_label: str | None, entries: Sequence[str], custom_entries: Sequence[str]
    ) -> Sequence[str]:
        ...

    def agent_title_to_role(self, title: str | None) -> AgentRole | None:
        ...

    def agent_tone_to_enum(self, tone: str | None) -> AgentTone | None:
        ...

    def agent_traits_to_enums(self, traits: Sequence[str]) -> Sequence[AgentTrait]:
        ...

    def escalation_label_to_enum(self, label: str | None) -> EscalationRule | None:
        ...


UrlNormalizer = Callable[[str], str]


@dataclass(slots=True, frozen=True)
class StartRegistrationInput:
    first_name: str
    email: str
    password_hash: str | None = None
    auth_provider: str | None = None
    idempotency_key: str | None = None


@dataclass(slots=True, frozen=True)
class StartRegistrationResult:
    registration: RegistrationSessionRecord
    user: UserRecord
    next_step: str


@dataclass(slots=True, frozen=True)
class UpsertBusinessInput:
    registration_id: uuid.UUID
    user_id: uuid.UUID
    business_name: str
    industry_label: str
    specify_industry: str | None
    line_of_business: Sequence[str] = field(default_factory=tuple)
    line_of_business_custom: Sequence[str] = field(default_factory=tuple)
    country: str | None = None
    website: str | None = None
    idempotency_key: str | None = None


@dataclass(slots=True, frozen=True)
class UpsertBusinessResult:
    business: BusinessRecord
    session: RegistrationSessionRecord
    niches: Sequence[str]


@dataclass(slots=True, frozen=True)
class ConfigureAgentInput:
    business_id: uuid.UUID
    user_id: uuid.UUID
    agent_name: str | None
    agent_title: str | None
    agent_tone: str | None
    agent_traits: Sequence[str]
    agent_escalation: str | None
    idempotency_key: str | None = None


@dataclass(slots=True, frozen=True)
class ConfigureAgentResult:
    agent: AgentWithTraitsRecord | None
    session: RegistrationSessionRecord


@dataclass(slots=True, frozen=True)
class AttachUploadLinksInput:
    business_id: uuid.UUID
    user_id: uuid.UUID
    links: Mapping[str, Sequence[str]]
    language: str | None = None
    idempotency_key: str | None = None


@dataclass(slots=True, frozen=True)
class AttachUploadLinksResult:
    created_counts: Mapping[str, int]
    duplicate_count: int
    session: RegistrationSessionRecord


@dataclass(slots=True, frozen=True)
class CompleteRegistrationInput:
    registration_id: uuid.UUID
    user_id: uuid.UUID


@dataclass(slots=True, frozen=True)
class CompletionStatus:
    session: RegistrationSessionRecord
    progress: Mapping[str, Any]


@dataclass(slots=True)
class _RepositoryBundle:
    sessions: RegistrationSessionsRepository
    users: UsersRepository
    businesses: BusinessesRepository
    business_niches: BusinessNichesRepository
    industry_catalog: IndustryCatalogRepository
    memberships: MembershipsRepository
    agents: AgentsRepository
    knowledge_items: KnowledgeItemsRepository


def _default_normalize_url(raw: str) -> str:
    url = raw.strip()
    if not url:
        raise ServiceValidationError("URL is empty", details={"url": raw})
    if not url.startswith("https://"):
        raise ServiceValidationError("Only https URLs are accepted", details={"url": url})
    return url.rstrip("/")


class RegistrationService:
    """Coordinates repositories to power the registration wizard."""

    def __init__(
        self,
        session: Session,
        *,
        catalog_mapper: RegistrationCatalogMapper,
        url_normalizer: UrlNormalizer | None = None,
    ) -> None:
        self._session = session
        self._catalog_mapper = catalog_mapper
        self._normalize_url = url_normalizer or _default_normalize_url

    @contextmanager
    def _transaction(self) -> Iterator[_RepositoryBundle]:
        with self._session.begin():
            bundle = _RepositoryBundle(
                sessions=RegistrationSessionsRepository(self._session),
                users=UsersRepository(self._session),
                businesses=BusinessesRepository(self._session),
                business_niches=BusinessNichesRepository(self._session),
                industry_catalog=IndustryCatalogRepository(self._session),
                memberships=MembershipsRepository(self._session),
                agents=AgentsRepository(self._session),
                knowledge_items=KnowledgeItemsRepository(self._session),
            )
            yield bundle

    def start_registration(self, params: StartRegistrationInput) -> StartRegistrationResult:
        payload_hash = self._hash_payload(
            {
                "firstName": params.first_name,
                "email": params.email.lower(),
                "authProvider": params.auth_provider or ("google" if params.password_hash is None else "password"),
                "key": params.idempotency_key,
            }
        )

        with self._transaction() as repos:
            email_normalized = params.email.strip()
            if not email_normalized:
                raise ServiceValidationError("Email is required")
            email_lower = email_normalized.lower()

            user_record = repos.users.find_by_email(email_lower=email_lower)
            if user_record is None:
                try:
                    user_record = repos.users.create_user(
                        first_name=params.first_name.strip(),
                        email=email_normalized,
                        password_hash=params.password_hash,
                        auth_provider=params.auth_provider
                        or ("google" if params.password_hash is None else "password"),
                    )
                except RepositoryValidationError as exc:
                    raise self._translate_repository_error(exc) from exc
                except RepositoryConflictError as exc:
                    # Race: user created concurrently, fetch again
                    user_record = repos.users.find_by_email(email_lower=email_lower)
                    if user_record is None:
                        raise self._translate_repository_error(exc) from exc

            active_sessions = repos.sessions.list_sessions(
                for_user_id=user_record.id,
                status="active",
                limit=1,
                sort_by="updated_at",
                order="desc",
            )

            if active_sessions:
                session_record = active_sessions[0]
            else:
                try:
                    session_record = repos.sessions.create_session(user_id=user_record.id)
                    session_state = self._merge_state(
                        session_record.state,
                        {
                            "form": {
                                "firstName": params.first_name.strip(),
                                "email": email_normalized,
                            },
                            "idempotency": {"form": payload_hash},
                        },
                    )
                    session_record = repos.sessions.update_progress(
                        registration_id=session_record.id,
                        for_user_id=user_record.id,
                        current_step=RegistrationStep.BUSINESS_PROFILE,
                        state_patch=session_state,
                        steps_completed=max(1, session_record.steps_completed),
                    )
                except RepositoryError as exc:
                    raise self._translate_repository_error(exc) from exc

        return StartRegistrationResult(
            registration=session_record,
            user=user_record,
            next_step="business",
        )

    def upsert_business_profile(self, params: UpsertBusinessInput) -> UpsertBusinessResult:
        payload_hash = self._hash_payload(
            {
                "businessName": params.business_name,
                "industry": params.industry_label,
                "specify": params.specify_industry,
                "lob": list(params.line_of_business),
                "lobCustom": list(params.line_of_business_custom),
                "country": params.country,
                "website": params.website,
                "key": params.idempotency_key,
            }
        )

        with self._transaction() as repos:
            try:
                session_record = repos.sessions.get_session(
                    registration_id=params.registration_id,
                    for_user_id=params.user_id,
                )
            except RepositoryError as exc:
                raise self._translate_repository_error(exc) from exc

            existing_hash = self._extract_idempotency_hash(session_record.state, "business")
            if existing_hash == payload_hash:
                business_record = self._load_business(repos, session_record)
                if business_record is not None:
                    niches = self._load_niches(repos, session_record.business_id)
                    return UpsertBusinessResult(
                        business=business_record,
                        session=session_record,
                        niches=niches,
                    )

            industry_code = self._catalog_mapper.industry_to_code(
                label=params.industry_label,
                custom=params.specify_industry,
            )
            if not industry_code:
                raise ServiceValidationError(
                    "Industry selection could not be mapped",
                    details={"industry": params.industry_label},
                )

            niche_codes = list(
                self._catalog_mapper.line_of_business_to_codes(
                    industry_label=params.industry_label,
                    entries=params.line_of_business,
                    custom_entries=params.line_of_business_custom,
                )
            )

            industry_label_source = (
                params.specify_industry.strip()
                if params.specify_industry
                else params.industry_label.strip()
            )
            repos.industry_catalog.ensure_industry(
                code=industry_code,
                label=industry_label_source
                or self._humanize_catalog_code(industry_code, prefix="industry:", default="Industry"),
            )

            if niche_codes:
                niche_label_map = self._build_niche_label_map(params)
                repos.industry_catalog.ensure_niches(
                    industry_code=industry_code,
                    items={
                        code: niche_label_map.get(
                            code,
                            self._humanize_catalog_code(
                                code,
                                prefix="niche:",
                                default="Niche",
                            ),
                        )
                        for code in niche_codes
                    },
                )

            business_record = self._load_business(repos, session_record)
            creator_name = self._extract_first_name(session_record.state)

            niches_records: list[BusinessNicheRecord]
            try:
                if business_record is None:
                    business_record = repos.businesses.create_business(
                        name=params.business_name.strip(),
                        industry_code=industry_code,
                        created_by_user_id=params.user_id,
                        created_by_user_name=creator_name,
                    )
                    session_record = repos.sessions.attach_business(
                        registration_id=session_record.id,
                        for_user_id=params.user_id,
                        business_id=business_record.id,
                    )
                    repos.memberships.add_owner(
                        user_id=params.user_id,
                        business_id=business_record.id,
                    )
                niches_records = repos.business_niches.replace_niches(
                    business_id=business_record.id,
                    niche_codes=niche_codes,
                )
            except RepositoryError as exc:
                raise self._translate_repository_error(exc) from exc

            session_state = self._merge_state(
                session_record.state,
                {
                    "business": {
                        "businessName": params.business_name.strip(),
                        "industryCode": industry_code,
                        "niches": niche_codes,
                        "country": params.country,
                        "website": params.website,
                    },
                    "idempotency": {"business": payload_hash},
                },
            )

            try:
                session_record = repos.sessions.update_progress(
                    registration_id=session_record.id,
                    for_user_id=params.user_id,
                    current_step=RegistrationStep.AGENT_SETUP,
                    state_patch=session_state,
                    steps_completed=max(2, session_record.steps_completed),
                )
            except RepositoryError as exc:
                raise self._translate_repository_error(exc) from exc

        return UpsertBusinessResult(
            business=business_record,
            session=session_record,
            niches=[record.niche_code for record in niches_records],
        )

    def configure_agent(self, params: ConfigureAgentInput) -> ConfigureAgentResult:
        payload_hash = self._hash_payload(
            {
                "name": params.agent_name,
                "title": params.agent_title,
                "tone": params.agent_tone,
                "traits": list(params.agent_traits),
                "escalation": params.agent_escalation,
                "key": params.idempotency_key,
            }
        )

        with self._transaction() as repos:
            session_record = self._get_session_for_business(repos, params.business_id, params.user_id)
            self._ensure_membership(repos, params.business_id, params.user_id)

            existing_hash = self._extract_idempotency_hash(session_record.state, "agent")
            if existing_hash == payload_hash:
                return ConfigureAgentResult(agent=None, session=session_record)

            traits_unique = list(dict.fromkeys(params.agent_traits))
            has_agent_payload = any([
                params.agent_name,
                params.agent_title,
                params.agent_tone,
                traits_unique,
                params.agent_escalation,
            ])

            agent_record: AgentWithTraitsRecord | None = None
            try:
                if has_agent_payload:
                    role = self._catalog_mapper.agent_title_to_role(params.agent_title)
                    if params.agent_title and role is None:
                        raise ServiceValidationError(
                            "Agent title is not supported",
                            details={"title": params.agent_title},
                        )

                    tone = self._catalog_mapper.agent_tone_to_enum(params.agent_tone)
                    if params.agent_tone and tone is None:
                        raise ServiceValidationError(
                            "Agent tone is not supported",
                            details={"tone": params.agent_tone},
                        )

                    traits = list(self._catalog_mapper.agent_traits_to_enums(traits_unique))
                    escalation = self._catalog_mapper.escalation_label_to_enum(params.agent_escalation)
                    if params.agent_escalation and escalation is None:
                        raise ServiceValidationError(
                            "Escalation rule is not supported",
                            details={"rule": params.agent_escalation},
                        )

                    creator_name = self._extract_first_name(session_record.state)
                    agent_core = repos.agents.create_agent(
                        business_id=params.business_id,
                        name=(params.agent_name or creator_name).strip(),
                        role=role or AgentRole.SUPPORT,
                        tone=tone or AgentTone.FRIENDLY,
                        escalation_rule=escalation or EscalationRule.ON_FALLBACK,
                        created_by_user_id=params.user_id,
                        created_by_user_name=creator_name,
                    )
                    repos.agents.replace_traits(
                        business_id=params.business_id,
                        agent_id=agent_core.id,
                        trait_codes=traits,
                    )
                    agent_record = repos.agents.get_agent_with_traits(
                        business_id=params.business_id,
                        agent_id=agent_core.id,
                    )
            except RepositoryError as exc:
                raise self._translate_repository_error(exc) from exc

            next_step = RegistrationStep.KNOWLEDGE_UPLOADS
            steps_completed = session_record.steps_completed
            if has_agent_payload:
                steps_completed = max(3, steps_completed)

            session_state = self._merge_state(
                session_record.state,
                {
                    "agent": {
                        "hasAgent": has_agent_payload,
                        "payloadHash": payload_hash,
                    },
                    "idempotency": {"agent": payload_hash},
                },
            )

            try:
                session_record = repos.sessions.update_progress(
                    registration_id=session_record.id,
                    for_user_id=params.user_id,
                    current_step=next_step,
                    state_patch=session_state,
                    steps_completed=steps_completed,
                )
            except RepositoryError as exc:
                raise self._translate_repository_error(exc) from exc

        return ConfigureAgentResult(agent=agent_record, session=session_record)

    def attach_upload_links(self, params: AttachUploadLinksInput) -> AttachUploadLinksResult:
        total_links = sum(len(values) for values in params.links.values())
        if total_links == 0:
            raise ServiceValidationError("At least one URL must be provided")
        if total_links > 50:
            raise ServiceValidationError(
                "At most 50 URLs may be attached per request",
                details={"count": total_links},
            )

        payload_hash = self._hash_payload(
            {
                "links": {key: list(values) for key, values in params.links.items()},
                "language": params.language,
                "key": params.idempotency_key,
            }
        )

        with self._transaction() as repos:
            session_record = self._get_session_for_business(repos, params.business_id, params.user_id)
            self._ensure_membership(repos, params.business_id, params.user_id)

            existing_hash = self._extract_idempotency_hash(session_record.state, "uploads")
            if existing_hash == payload_hash:
                return AttachUploadLinksResult(
                    created_counts={},
                    duplicate_count=0,
                    session=session_record,
                )

            completion_snapshot = repos.sessions.compute_step_completion(
                registration_id=session_record.id,
                for_user_id=params.user_id,
            )
            existing_knowledge = completion_snapshot.get("knowledge_count", 0)

            if existing_knowledge >= 500:
                raise ServiceValidationError(
                    "Knowledge attachment limit reached",
                    details={"limit": 500},
                )

            existing_items = repos.knowledge_items.list_items(
                business_id=params.business_id,
                source_type=[KnowledgeSource.URL],
                limit=500,
                offset=0,
                sort_by="created_at",
                order="desc",
            )
            seen_item_ids: set[uuid.UUID] = {item.id for item in existing_items}

            created_counts: dict[str, int] = {}
            duplicate_count = 0

            def normalized_entries(entries: Sequence[str]) -> list[str]:
                result: list[str] = []
                for raw in entries:
                    normalized = self._normalize_url(raw)
                    if normalized not in result:
                        result.append(normalized)
                return result

            display_map = {
                "vision": "Vision",
                "mission": "Mission",
                "catalog": "Products & Services Catalog",
                "faqs": "FAQs",
                "kb": "Knowledge Base",
                "sops": "SOPs",
                "tc": "T&C",
            }

            new_total = existing_knowledge

            for category, urls in params.links.items():
                unique_urls = normalized_entries(urls)
                if not unique_urls:
                    continue
                created_for_category = 0
                display_name = display_map.get(category, category.title())
                for url in unique_urls:
                    if new_total >= 500:
                        raise ServiceValidationError(
                            "Knowledge attachment limit reached",
                            details={"limit": 500},
                        )
                    try:
                        record: KnowledgeItemWithDetailRecord = repos.knowledge_items.create_url(
                            business_id=params.business_id,
                            display_name=display_name,
                            url=url,
                            language=params.language,
                            created_by_user_id=params.user_id,
                            created_by_user_name=self._extract_first_name(session_record.state),
                        )
                    except RepositoryError as exc:
                        raise self._translate_repository_error(exc) from exc

                    if record.item.id in seen_item_ids:
                        duplicate_count += 1
                    else:
                        created_for_category += 1
                        new_total += 1
                        seen_item_ids.add(record.item.id)
                if created_for_category:
                    created_counts[category] = created_for_category

            session_state = self._merge_state(
                session_record.state,
                {
                    "uploads": {
                        "categories": {
                            key: list(value)
                            for key, value in (
                                (cat, params.links[cat])
                                for cat in params.links
                            )
                        },
                        "payloadHash": payload_hash,
                    },
                    "idempotency": {"uploads": payload_hash},
                },
            )

            try:
                if new_total > 0:
                    session_record = repos.sessions.mark_completed(
                        registration_id=session_record.id,
                        for_user_id=params.user_id,
                    )
                session_record = repos.sessions.update_progress(
                    registration_id=session_record.id,
                    for_user_id=params.user_id,
                    current_step=RegistrationStep.COMPLETED if new_total > 0 else session_record.current_step,
                    state_patch=session_state,
                    steps_completed=max(session_record.steps_completed, 4 if new_total > 0 else session_record.steps_completed),
                )
            except RepositoryError as exc:
                raise self._translate_repository_error(exc) from exc

        return AttachUploadLinksResult(
            created_counts=created_counts,
            duplicate_count=duplicate_count,
            session=session_record,
        )

    def complete_registration(self, params: CompleteRegistrationInput) -> CompletionStatus:
        with self._transaction() as repos:
            try:
                session_record = repos.sessions.get_session(
                    registration_id=params.registration_id,
                    for_user_id=params.user_id,
                )
            except RepositoryError as exc:
                raise self._translate_repository_error(exc) from exc

            if session_record.business_id is None:
                raise ServiceConflictError(
                    "Registration cannot be completed before business profile",
                    details={"registrationId": str(params.registration_id)},
                )

            progress = repos.sessions.compute_step_completion(
                registration_id=params.registration_id,
                for_user_id=params.user_id,
            )

            ready = bool(progress.get("knowledge_count", 0)) or progress.get("agent_configured", False)
            if not ready:
                raise ServiceConflictError(
                    "Completion requires at least one agent or knowledge source",
                    details={"registrationId": str(params.registration_id)},
                )

            try:
                session_record = repos.sessions.mark_completed(
                    registration_id=params.registration_id,
                    for_user_id=params.user_id,
                )
            except RepositoryError as exc:
                raise self._translate_repository_error(exc) from exc

        return CompletionStatus(session=session_record, progress=progress)

    def _get_session_for_business(
        self,
        repos: _RepositoryBundle,
        business_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> RegistrationSessionRecord:
        try:
            sessions = repos.sessions.list_sessions(
                for_user_id=user_id,
                status="all",
                limit=20,
                offset=0,
                sort_by="updated_at",
                order="desc",
            )
        except RepositoryError as exc:
            raise self._translate_repository_error(exc) from exc

        current_time = utc_now()
        for record in sessions:
            if record.business_id == business_id:
                if record.expires_at <= current_time:
                    raise ServiceExpiredError(
                        "Registration session expired",
                        details={"registrationId": str(record.id)},
                    )
                return record

        raise ServiceNotFoundError(
            "Registration session not found for business",
            details={"businessId": str(business_id)},
        )

    def _load_business(
        self,
        repos: _RepositoryBundle,
        session_record: RegistrationSessionRecord,
    ) -> BusinessRecord | None:
        if session_record.business_id is None:
            return None
        try:
            return repos.businesses.get_business(business_id=session_record.business_id)
        except RepositoryNotFoundError:
            return None
        except RepositoryError as exc:
            raise self._translate_repository_error(exc) from exc

    def _load_niches(
        self,
        repos: _RepositoryBundle,
        business_id: uuid.UUID | None,
    ) -> list[str]:
        if business_id is None:
            return []
        try:
            return [record.niche_code for record in repos.business_niches.list_niches(business_id=business_id)]
        except RepositoryError as exc:
            raise self._translate_repository_error(exc) from exc

    def _build_niche_label_map(self, params: UpsertBusinessInput) -> dict[str, str]:
        label_map: dict[str, str] = {}

        for value in params.line_of_business:
            candidate = value.strip()
            if not candidate:
                continue
            code = self._resolve_single_niche_code(
                industry_label=params.industry_label,
                value=candidate,
                is_custom=False,
            )
            if code and code not in label_map:
                label_map[code] = candidate

        for value in params.line_of_business_custom:
            candidate = value.strip()
            if not candidate:
                continue
            code = self._resolve_single_niche_code(
                industry_label=params.industry_label,
                value=candidate,
                is_custom=True,
            )
            if code and code not in label_map:
                label_map[code] = candidate

        return label_map

    def _resolve_single_niche_code(
        self,
        *,
        industry_label: str,
        value: str,
        is_custom: bool,
    ) -> str | None:
        entries = [value] if not is_custom else []
        custom_entries = [value] if is_custom else []
        codes = self._catalog_mapper.line_of_business_to_codes(
            industry_label=industry_label,
            entries=entries,
            custom_entries=custom_entries,
        )
        if not codes:
            return None
        return codes[0]

    def _humanize_catalog_code(self, code: str, *, prefix: str, default: str) -> str:
        code_lower = code.lower()
        if not code_lower.startswith(prefix):
            return default
        remainder = code[len(prefix) :]
        parts = [segment for segment in remainder.split("-") if segment]
        if not parts:
            return default
        return " ".join(segment.capitalize() for segment in parts)

    def _ensure_membership(
        self,
        repos: _RepositoryBundle,
        business_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        try:
            roles = repos.memberships.get_roles(business_id=business_id, user_id=user_id)
        except RepositoryError as exc:
            raise self._translate_repository_error(exc) from exc
        if roles:
            return
        try:
            repos.memberships.add_owner(user_id=user_id, business_id=business_id)
        except RepositoryError as exc:
            raise self._translate_repository_error(exc) from exc

    def _extract_first_name(self, state: Mapping[str, Any] | None) -> str:
        if not isinstance(state, Mapping):
            return "Owner"
        form = state.get("form")
        if isinstance(form, Mapping):
            first_name = form.get("firstName")
            if isinstance(first_name, str) and first_name.strip():
                return first_name.strip()
        return "Owner"

    def _extract_idempotency_hash(
        self, state: Mapping[str, Any] | None, step_key: str
    ) -> str | None:
        if not isinstance(state, Mapping):
            return None
        idempotency = state.get("idempotency")
        if isinstance(idempotency, Mapping):
            value = idempotency.get(step_key)
            if isinstance(value, str):
                return value
        return None

    def _merge_state(
        self, original: Mapping[str, Any] | None, patch: Mapping[str, Any]
    ) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        if isinstance(original, Mapping):
            merged.update(self._deep_copy(original))
        self._deep_merge(merged, patch)
        return merged

    def _deep_copy(self, source: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in source.items():
            if isinstance(value, Mapping):
                result[key] = self._deep_copy(value)
            elif isinstance(value, list):
                result[key] = [self._deep_copy(item) if isinstance(item, Mapping) else item for item in value]
            else:
                result[key] = value
        return result

    def _deep_merge(self, target: MutableMapping[str, Any], patch: Mapping[str, Any]) -> None:
        for key, value in patch.items():
            if key in target and isinstance(target[key], MutableMapping) and isinstance(value, Mapping):
                self._deep_merge(target[key], value)  # type: ignore[arg-type]
            else:
                target[key] = value

    def _hash_payload(self, payload: Mapping[str, Any]) -> str:
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _translate_repository_error(self, error: RepositoryError) -> ServiceError:
        if isinstance(error, RepositoryValidationError):
            return ServiceValidationError(error.message, details=error.details)
        if isinstance(error, RepositoryConflictError):
            return ServiceConflictError(error.message, details=error.details)
        if isinstance(error, RepositoryNotFoundError):
            return ServiceNotFoundError(error.message, details=error.details)
        if isinstance(error, RepositoryExpiredError):
            return ServiceExpiredError(error.message, details=error.details)
        if isinstance(error, RepositoryTimeoutError):
            return ServiceTimeoutError(error.message, details=error.details)
        return ServiceError(code=error.code, message=error.message, details=error.details)


__all__ = [
    "AttachUploadLinksInput",
    "AttachUploadLinksResult",
    "CompleteRegistrationInput",
    "CompletionStatus",
    "ConfigureAgentInput",
    "ConfigureAgentResult",
    "RegistrationCatalogMapper",
    "RegistrationService",
    "StartRegistrationInput",
    "StartRegistrationResult",
    "UpsertBusinessInput",
    "UpsertBusinessResult",
]
