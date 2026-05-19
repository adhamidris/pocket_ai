from __future__ import annotations

from .oauth_shared import *  # noqa: F403

# ─────────────────────────────────────────────────────────────────────────────
# OAuth start / callback views
# ─────────────────────────────────────────────────────────────────────────────

@require_http_methods(["GET"])
def integration_oauth_start(request: HttpRequest, integration_type: str) -> HttpResponse:
    """Start OAuth flow for a native integration type."""

    config = _get_type_config(integration_type)
    if not config:
        return _popup_html(
            {"type": "integration_oauth_error", "error": "unknown_integration_type", "integration_type": integration_type},
            fallback_redirect=_default_redirect_after(),
        )

    business_param = request.GET.get("business_id") or request.GET.get("businessId")
    business, error = _resolve_business(request, business_param)
    if error:
        return error
    assert business is not None

    oauth_provider_key = config["oauth_provider_key"]
    provider = OAuthProvider.objects.filter(key=oauth_provider_key, is_active=True).first()
    if not provider:
        provider = _bootstrap_integration_oauth_provider(config)
    if not provider:
        return _popup_html(
            {
                "type": "integration_oauth_error",
                "error": "provider_not_configured",
                "integration_type": integration_type,
                "hint": f"Set {config['client_id_setting']}/{config['client_secret_setting']} (or configure OAuthProvider in admin).",
            },
            fallback_redirect=_default_redirect_after(),
        )

    scopes = provider.scopes if isinstance(provider.scopes, list) else []
    if not scopes:
        return _popup_html(
            {"type": "integration_oauth_error", "error": "provider_missing_scopes", "integration_type": integration_type},
            fallback_redirect=_default_redirect_after(),
        )

    redirect_after = _safe_redirect_after(request, request.GET.get("redirect"))

    state_token = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    challenge = _pkce_challenge(code_verifier)

    callback_path = reverse(
        "api:integration_oauth_callback",
        kwargs={"integration_type": str(integration_type).strip().lower()},
    )

    base_url = str(getattr(settings, "API_PUBLIC_URL", "") or "").strip()
    if base_url:
        callback_url = f"{base_url.rstrip('/')}{callback_path}"
    else:
        callback_url = request.build_absolute_uri(callback_path)

    IntegrationOAuthState.objects.create(
        business_profile=business,
        user=request.user,
        provider=provider,
        integration_type=integration_type,
        state_token=state_token,
        redirect_after=redirect_after,
        redirect_uri=callback_url,
        code_verifier=code_verifier,
        expires_at=timezone.now() + timedelta(minutes=DEFAULT_STATE_TTL_MINUTES),
    )

    params: dict[str, str] = {
        "client_id": provider.client_id,
        "redirect_uri": callback_url,
        "response_type": "code",
        "scope": " ".join([str(scope).strip() for scope in scopes if str(scope).strip()]),
        "state": state_token,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }

    profile_type = config.get("profile_type", "")
    if profile_type == "google":
        params.update({
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        })
    elif profile_type == "microsoft":
        params["response_mode"] = "query"
    elif profile_type == "slack":
        # Slack uses user_scope for user tokens
        params.pop("scope", None)
        params["user_scope"] = " ".join([str(scope).strip() for scope in scopes if str(scope).strip()])

    auth_url = f"{provider.authorization_url}?{urlencode(params)}"
    return redirect(auth_url)


@require_http_methods(["GET"])
def integration_oauth_callback(request: HttpRequest, integration_type: str) -> HttpResponse:
    """OAuth callback for native integrations."""

    config = _get_type_config(integration_type)
    if not config:
        return _popup_html(
            {"type": "integration_oauth_error", "error": "unknown_integration_type", "integration_type": integration_type},
            fallback_redirect=_default_redirect_after(),
        )

    redirect_fallback = _default_redirect_after()

    error_code = request.GET.get("error")
    if error_code:
        description = request.GET.get("error_description") or error_code
        return _popup_html(
            {"type": "integration_oauth_error", "error": str(error_code), "detail": str(description)[:200]},
            fallback_redirect=redirect_fallback,
        )

    code = request.GET.get("code")
    state_token = request.GET.get("state")
    if not code or not state_token:
        return _popup_html(
            {"type": "integration_oauth_error", "error": "missing_params"},
            fallback_redirect=redirect_fallback,
        )

    oauth_state = (
        IntegrationOAuthState.objects.select_related("provider", "business_profile", "user")
        .filter(
            state_token=state_token,
            integration_type=integration_type,
            provider__key=config["oauth_provider_key"],
            is_used=False,
            expires_at__gt=timezone.now(),
        )
        .first()
    )
    if not oauth_state:
        return _popup_html(
            {"type": "integration_oauth_error", "error": "invalid_state"},
            fallback_redirect=redirect_fallback,
        )

    if request.user.is_authenticated and not request.user.is_staff and request.user.id != oauth_state.user_id:
        return _popup_html(
            {"type": "integration_oauth_error", "error": "forbidden"},
            fallback_redirect=redirect_fallback,
        )

    oauth_state.is_used = True
    oauth_state.save(update_fields=["is_used"])

    provider = oauth_state.provider
    business = oauth_state.business_profile

    scope_param = None
    profile_type = config.get("profile_type", "")
    if profile_type in {"microsoft", "hubspot"}:
        scopes = provider.scopes if isinstance(provider.scopes, list) else []
        scope_param = " ".join([str(scope).strip() for scope in scopes if str(scope).strip()]) or None

    try:
        token_payload = exchange_authorization_code(
            provider,
            code=str(code),
            redirect_uri=oauth_state.redirect_uri or "",
            scope=scope_param,
            code_verifier=oauth_state.code_verifier or None,
        )
    except OAuthFlowError as exc:
        return _popup_html(
            {"type": "integration_oauth_error", "error": "token_exchange_failed", "detail": str(exc)[:200]},
            fallback_redirect=redirect_fallback,
        )

    # Slack v2 returns tokens in authed_user sub-object
    if profile_type == "slack" and isinstance(token_payload.get("authed_user"), dict):
        token_payload = {**token_payload, **token_payload["authed_user"]}

    access_token = str(token_payload.get("access_token") or "").strip()
    if not access_token:
        return _popup_html(
            {"type": "integration_oauth_error", "error": "no_access_token"},
            fallback_redirect=redirect_fallback,
        )

    expires_at = compute_expires_at(token_payload)
    refresh_token = str(token_payload.get("refresh_token") or "").strip() or None
    token_type = str(token_payload.get("token_type") or token_payload.get("tokenType") or "Bearer").strip() or "Bearer"

    fetcher = _PROFILE_FETCHERS.get(profile_type)
    if not fetcher:
        return _popup_html(
            {"type": "integration_oauth_error", "error": "no_profile_fetcher"},
            fallback_redirect=redirect_fallback,
        )

    try:
        profile = fetcher(access_token)
    except OAuthFlowError as exc:
        return _popup_html(
            {"type": "integration_oauth_error", "error": "profile_failed", "detail": str(exc)[:200]},
            fallback_redirect=redirect_fallback,
        )

    identifier = profile.get("identifier", "")
    external_id = profile.get("external_id", "")
    display_name = profile.get("display_name", "")

    created = False
    with tenant_context(business.id):
        account = IntegrationAccount.objects.filter(
            business_profile=business,
            user=oauth_state.user,
            integration_type=integration_type,
        ).first()
        if not account:
            account = IntegrationAccount(
                business_profile=business,
                user=oauth_state.user,
                integration_type=integration_type,
                provider=config["provider"],
            )
            created = True
        account.provider = config["provider"]
        account.account_identifier = identifier
        if external_id:
            account.external_account_id = external_id
        account.status = IntegrationAccountStatus.CONNECTED
        account.last_error = ""

        credentials = dict(account.credentials or {})
        existing_refresh = str(credentials.get("refresh_token") or "").strip() or None
        credentials["access_token"] = access_token
        if refresh_token or existing_refresh:
            credentials["refresh_token"] = refresh_token or existing_refresh
        if expires_at is not None:
            credentials["expires_at"] = expires_at.isoformat()
        if token_type:
            credentials["token_type"] = token_type
        scope_value = token_payload.get("scope")
        if scope_value:
            credentials["scope"] = scope_value
        credentials["provider"] = config["provider"]
        credentials["updated_at"] = timezone.now().isoformat()
        account.credentials = credentials

        meta = dict(account.metadata or {}) if isinstance(account.metadata, dict) else {}
        meta["profile"] = {
            "identifier": identifier,
            "name": display_name,
            "provider": config["provider"],
            "linked_at": timezone.now().isoformat(),
        }
        account.metadata = meta

        account.save()

        IntegrationAccountAuditEvent.objects.create(
            business_profile=business,
            integration_account=account,
            integration_account_id_snapshot=account.id,
            actor_user=oauth_state.user,
            action=IntegrationAccountAuditAction.CONNECTED if created else IntegrationAccountAuditAction.UPDATED,
            description=f"{config['name']} integration connected via OAuth.",
            metadata={
                "integration_type": integration_type,
                "provider": config["provider"],
                "identifier_sha256": _sha256_hex(identifier),
            },
        )

    redirect_after = oauth_state.redirect_after or redirect_fallback
    return _popup_html(
        {
            "type": "integration_oauth_success",
            "integration_type": integration_type,
            "provider": config["provider"],
            "identifier": identifier,
        },
        fallback_redirect=redirect_after,
    )


@csrf_protect
@require_http_methods(["POST"])
def integration_oauth_disconnect(request: HttpRequest, integration_type: str) -> JsonResponse:
    """Disconnect a native OAuth integration account for the current business + actor."""

    config = _get_type_config(integration_type)
    if not config:
        return JsonResponse(
            {"error": "UNKNOWN_INTEGRATION_TYPE", "message": "Unknown integration type."},
            status=HTTPStatus.NOT_FOUND,
        )

    payload, payload_error = _parse_json_payload(request)
    if payload_error:
        return payload_error

    business_param = (
        payload.get("businessId")
        or payload.get("business_id")
        or request.GET.get("business_id")
        or request.GET.get("businessId")
    )
    business, business_error = _resolve_business_api(request, str(business_param or ""))
    if business_error:
        return business_error
    assert business is not None

    normalized_type = str(integration_type or "").strip().lower()

    with tenant_context(business.id):
        account_qs = IntegrationAccount.objects.filter(
            business_profile=business,
            integration_type=normalized_type,
            user=request.user,
        )
        account = account_qs.order_by("-updated_at").first()
        if not account:
            return JsonResponse(
                {
                    "error": "INTEGRATION_ACCOUNT_NOT_FOUND",
                    "message": "No integration account found for this provider.",
                },
                status=HTTPStatus.NOT_FOUND,
            )

        identifier = str(account.account_identifier or "")
        account.status = IntegrationAccountStatus.DISCONNECTED
        account.last_error = ""
        account.credentials = {}

        metadata = dict(account.metadata or {}) if isinstance(account.metadata, dict) else {}
        profile_meta = metadata.get("profile")
        if isinstance(profile_meta, dict):
            profile_meta["disconnected_at"] = timezone.now().isoformat()
            metadata["profile"] = profile_meta
        else:
            metadata["disconnected_at"] = timezone.now().isoformat()
        account.metadata = metadata

        account.save(
            update_fields=[
                "status",
                "last_error",
                "metadata",
                "credentials_encrypted",
                "credentials_key_version",
                "credentials_last_rotated_at",
                "credential_error_count",
                "updated_at",
            ]
        )

        IntegrationAccountAuditEvent.objects.create(
            business_profile=business,
            integration_account=account,
            integration_account_id_snapshot=account.id,
            actor_user=request.user if request.user.is_authenticated else None,
            action=IntegrationAccountAuditAction.DISCONNECTED,
            description=f"{config['name']} integration disconnected.",
            metadata={
                "integration_type": normalized_type,
                "provider": config["provider"],
                "identifier_sha256": _sha256_hex(identifier),
            },
        )

    return JsonResponse(
        {
            "status": IntegrationAccountStatus.DISCONNECTED,
            "integration_type": normalized_type,
            "provider": config["provider"],
        },
        status=HTTPStatus.OK,
    )


@csrf_protect
@require_http_methods(["GET", "POST"])
def integration_oauth_tools(request: HttpRequest, integration_type: str) -> JsonResponse:
    """List/update per-tool enabled settings for a connected native integration account."""

    config = _get_type_config(integration_type)
    if not config:
        return JsonResponse(
            {"error": "UNKNOWN_INTEGRATION_TYPE", "message": "Unknown integration type."},
            status=HTTPStatus.NOT_FOUND,
        )

    payload: dict[str, object] = {}
    if request.method != "GET":
        payload, payload_error = _parse_json_payload(request)
        if payload_error:
            return payload_error

    business_param = (
        payload.get("businessId")
        or payload.get("business_id")
        or request.GET.get("business_id")
        or request.GET.get("businessId")
    )
    business, business_error = _resolve_business_api(request, str(business_param or ""))
    if business_error:
        return business_error
    assert business is not None

    normalized_type = str(integration_type or "").strip().lower()

    with tenant_context(business.id):
        account = (
            IntegrationAccount.objects.filter(
                business_profile=business,
                integration_type=normalized_type,
                user=request.user,
                status=IntegrationAccountStatus.CONNECTED,
            )
            .order_by("-updated_at")
            .first()
        )
        if not account:
            return JsonResponse(
                {
                    "error": "INTEGRATION_ACCOUNT_NOT_FOUND",
                    "message": "No connected integration account found for this provider.",
                },
                status=HTTPStatus.NOT_FOUND,
            )

        if request.method == "POST":
            updates = _normalize_native_tool_updates(integration_type=normalized_type, payload=payload)
            metadata = dict(account.metadata or {}) if isinstance(account.metadata, dict) else {}
            raw_settings = metadata.get("tool_settings")
            if raw_settings is None:
                raw_settings = metadata.get("toolSettings")
            settings_map = dict(raw_settings) if isinstance(raw_settings, Mapping) else {}
            now_iso = timezone.now().isoformat()
            applied: list[dict[str, object]] = []
            for item in updates:
                tool_name = str(item.get("toolName") or "").strip()
                enabled = bool(item.get("enabled"))
                if not tool_name:
                    continue
                if enabled:
                    settings_map.pop(tool_name, None)
                else:
                    settings_map[tool_name] = {"enabled": False, "updated_at": now_iso}
                applied.append({"toolName": tool_name, "enabled": enabled})
            if settings_map:
                metadata["tool_settings"] = settings_map
            else:
                metadata.pop("tool_settings", None)
                metadata.pop("toolSettings", None)
            account.metadata = metadata
            account.save(update_fields=["metadata", "updated_at"])

            if applied:
                IntegrationAccountAuditEvent.objects.create(
                    business_profile=business,
                    integration_account=account,
                    integration_account_id_snapshot=account.id,
                    actor_user=request.user if request.user.is_authenticated else None,
                    action=IntegrationAccountAuditAction.UPDATED,
                    description=f"{config['name']} tool settings updated.",
                    metadata={
                        "integration_type": normalized_type,
                        "provider": config["provider"],
                        "changed_tools": len(applied),
                    },
                )

        tools_payload, summary = _serialize_native_tools_for_account(account=account, integration_type=normalized_type)

    return JsonResponse(
        {
            "businessId": str(business.id),
            "integrationType": normalized_type,
            "integrationLabel": config["name"],
            "provider": config["provider"],
            "accountId": str(account.id),
            "accountIdentifier": str(account.account_identifier or ""),
            "status": str(account.status or ""),
            "summary": summary,
            "tools": tools_payload,
        },
        status=HTTPStatus.OK,
    )
