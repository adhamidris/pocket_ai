from __future__ import annotations

from .oauth_shared import *  # noqa: F403

@require_http_methods(["GET"])
def email_oauth_start(request: HttpRequest, provider_key: str) -> HttpResponse:
    """
    Start OAuth flow for first-party email connectors.

    Intended usage: open this endpoint in a popup window and let it redirect to the provider.
    """

    mapping = _provider_mapping(provider_key)
    if not mapping:
        return _popup_html(
            {"type": "email_oauth_error", "error": "unknown_provider", "provider": provider_key},
            fallback_redirect=_default_redirect_after(),
        )

    email_provider, oauth_provider_key = mapping
    business_param = request.GET.get("business_id") or request.GET.get("businessId")
    business, error = _resolve_business(request, business_param)
    if error:
        return error
    assert business is not None

    provider = OAuthProvider.objects.filter(key=oauth_provider_key, is_active=True).first()
    if not provider:
        provider = _bootstrap_email_oauth_provider(oauth_provider_key)
    if not provider:
        return _popup_html(
            {
                "type": "email_oauth_error",
                "error": "provider_not_configured",
                "provider": str(provider_key),
                "hint": "Set EMAIL_OAUTH_<PROVIDER>_CLIENT_ID/SECRET (or configure OAuthProvider in admin).",
            },
            fallback_redirect=_default_redirect_after(),
        )

    scopes = provider.scopes if isinstance(provider.scopes, list) else []
    if not scopes:
        return _popup_html(
            {"type": "email_oauth_error", "error": "provider_missing_scopes", "provider": str(provider_key)},
            fallback_redirect=_default_redirect_after(),
        )

    redirect_after = _safe_redirect_after(request, request.GET.get("redirect"))

    state_token = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    challenge = _pkce_challenge(code_verifier)

    callback_path = reverse("api:email_oauth_callback", kwargs={"provider_key": str(provider_key).strip().lower()})
    
    # Allow override via settings, useful for local dev when request.get_host() (e.g. 8000)
    # differs from the registered OAuth redirect URI (e.g. 3000).
    base_url = str(getattr(settings, "API_PUBLIC_URL", "") or "").strip()
    if base_url:
        # Strip trailing slash from base and ensure path starts with slash
        callback_url = f"{base_url.rstrip('/')}{callback_path}"
    else:
        callback_url = request.build_absolute_uri(callback_path)

    EmailOAuthState.objects.create(
        business_profile=business,
        user=request.user,
        provider=provider,
        email_provider=email_provider,
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

    if email_provider == EmailAccountProvider.GOOGLE:
        params.update(
            {
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true",
            }
        )
    else:
        params["response_mode"] = "query"

    auth_url = f"{provider.authorization_url}?{urlencode(params)}"
    return redirect(auth_url)


@require_http_methods(["GET"])
def email_oauth_callback(request: HttpRequest, provider_key: str) -> HttpResponse:
    """
    OAuth callback for first-party email connectors.

    GET /api/email/oauth/callback/google/?code=...&state=...
    """

    mapping = _provider_mapping(provider_key)
    if not mapping:
        return _popup_html(
            {"type": "email_oauth_error", "error": "unknown_provider", "provider": provider_key},
            fallback_redirect=_default_redirect_after(),
        )

    email_provider, oauth_provider_key = mapping
    redirect_fallback = _default_redirect_after()

    error_code = request.GET.get("error")
    if error_code:
        description = request.GET.get("error_description") or error_code
        return _popup_html(
            {"type": "email_oauth_error", "error": str(error_code), "detail": str(description)[:200]},
            fallback_redirect=redirect_fallback,
        )

    code = request.GET.get("code")
    state_token = request.GET.get("state")
    if not code or not state_token:
        return _popup_html(
            {"type": "email_oauth_error", "error": "missing_params"},
            fallback_redirect=redirect_fallback,
        )

    oauth_state = (
        EmailOAuthState.objects.select_related("provider", "business_profile", "user")
        .filter(
            state_token=state_token,
            email_provider=email_provider,
            provider__key=oauth_provider_key,
            is_used=False,
            expires_at__gt=timezone.now(),
        )
        .first()
    )
    if not oauth_state:
        return _popup_html(
            {"type": "email_oauth_error", "error": "invalid_state"},
            fallback_redirect=redirect_fallback,
        )

    if request.user.is_authenticated and not request.user.is_staff and request.user.id != oauth_state.user_id:
        return _popup_html(
            {"type": "email_oauth_error", "error": "forbidden"},
            fallback_redirect=redirect_fallback,
        )

    oauth_state.is_used = True
    oauth_state.save(update_fields=["is_used"])

    provider = oauth_state.provider
    business = oauth_state.business_profile

    scope_param = None
    if email_provider == EmailAccountProvider.MICROSOFT:
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
            {"type": "email_oauth_error", "error": "token_exchange_failed", "detail": str(exc)[:200]},
            fallback_redirect=redirect_fallback,
        )

    access_token = str(token_payload.get("access_token") or "").strip()
    if not access_token:
        return _popup_html(
            {"type": "email_oauth_error", "error": "no_access_token"},
            fallback_redirect=redirect_fallback,
        )

    expires_at = compute_expires_at(token_payload)
    refresh_token = str(token_payload.get("refresh_token") or "").strip() or None
    token_type = str(token_payload.get("token_type") or token_payload.get("tokenType") or "Bearer").strip() or "Bearer"

    try:
        if email_provider == EmailAccountProvider.GOOGLE:
            profile = _fetch_google_userinfo(access_token)
            email_address = str(profile.get("email") or "").strip()
            external_id = str(profile.get("sub") or profile.get("id") or "").strip()
            display_name = str(profile.get("name") or "").strip()
        else:
            profile = _fetch_microsoft_profile(access_token)
            email_address = str(profile.get("mail") or profile.get("userPrincipalName") or "").strip()
            external_id = str(profile.get("id") or "").strip()
            display_name = str(profile.get("displayName") or "").strip()
    except OAuthFlowError as exc:
        return _popup_html(
            {"type": "email_oauth_error", "error": "profile_failed", "detail": str(exc)[:200]},
            fallback_redirect=redirect_fallback,
        )

    if not email_address:
        return _popup_html(
            {"type": "email_oauth_error", "error": "missing_email"},
            fallback_redirect=redirect_fallback,
        )

    created = False
    with tenant_context(business.id):
        account = EmailAccount.objects.filter(business_profile=business, user=oauth_state.user).first()
        if not account:
            account = EmailAccount(
                business_profile=business,
                user=oauth_state.user,
                provider=email_provider,
            )
            created = True
        account.provider = email_provider
        account.email_address = email_address
        if external_id:
            account.external_account_id = external_id
        account.status = EmailAccountStatus.CONNECTED
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
        credentials["provider"] = email_provider
        credentials["updated_at"] = timezone.now().isoformat()
        account.credentials = credentials

        meta = dict(account.metadata or {}) if isinstance(account.metadata, dict) else {}
        meta["email_profile"] = {
            "email": email_address,
            "name": display_name,
            "provider": email_provider,
            "linked_at": timezone.now().isoformat(),
        }
        account.metadata = meta

        account.save()

        EmailAccountAuditEvent.objects.create(
            business_profile=business,
            email_account=account,
            email_account_id_snapshot=account.id,
            actor_user=oauth_state.user,
            action=EmailAccountAuditAction.CONNECTED if created else EmailAccountAuditAction.UPDATED,
            description="Email account connected via OAuth.",
            metadata={
                "provider": email_provider,
                "email_sha256": _sha256_hex(email_address),
            },
        )

    redirect_after = oauth_state.redirect_after or redirect_fallback
    return _popup_html(
        {
            "type": "email_oauth_success",
            "provider": email_provider,
            "email": email_address,
        },
        fallback_redirect=redirect_after,
    )


@csrf_protect
@require_http_methods(["POST"])
def email_oauth_disconnect(request: HttpRequest, provider_key: str) -> JsonResponse:
    """Disconnect a first-party email OAuth account for the current business + actor."""

    mapping = _provider_mapping(provider_key)
    if not mapping:
        return JsonResponse(
            {"error": "UNKNOWN_PROVIDER", "message": "Unknown email provider."},
            status=HTTPStatus.NOT_FOUND,
        )
    email_provider, _oauth_provider_key = mapping

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

    with tenant_context(business.id):
        account_qs = EmailAccount.objects.filter(
            business_profile=business,
            provider=email_provider,
            user=request.user,
        )
        account = account_qs.order_by("-updated_at").first()
        if not account:
            return JsonResponse(
                {"error": "EMAIL_ACCOUNT_NOT_FOUND", "message": "No email account found for this provider."},
                status=HTTPStatus.NOT_FOUND,
            )

        email_address = str(account.email_address or "")
        account.status = EmailAccountStatus.DISCONNECTED
        account.last_error = ""
        account.credentials = {}

        metadata = dict(account.metadata or {}) if isinstance(account.metadata, dict) else {}
        profile_meta = metadata.get("email_profile")
        if isinstance(profile_meta, dict):
            profile_meta["disconnected_at"] = timezone.now().isoformat()
            metadata["email_profile"] = profile_meta
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

        EmailAccountAuditEvent.objects.create(
            business_profile=business,
            email_account=account,
            email_account_id_snapshot=account.id,
            actor_user=request.user if request.user.is_authenticated else None,
            action=EmailAccountAuditAction.DISCONNECTED,
            description="Email account disconnected.",
            metadata={
                "provider": email_provider,
                "email_sha256": _sha256_hex(email_address),
            },
        )

    return JsonResponse(
        {
            "status": EmailAccountStatus.DISCONNECTED,
            "provider": email_provider,
        },
        status=HTTPStatus.OK,
    )


@csrf_protect
@require_http_methods(["GET", "POST"])
def email_oauth_tools(request: HttpRequest, provider_key: str) -> JsonResponse:
    """List/update per-tool enabled settings for a connected native email account."""

    mapping = _provider_mapping(provider_key)
    if not mapping:
        return JsonResponse(
            {"error": "UNKNOWN_PROVIDER", "message": "Unknown email provider."},
            status=HTTPStatus.NOT_FOUND,
        )
    email_provider, _oauth_provider_key = mapping

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

    with tenant_context(business.id):
        account = (
            EmailAccount.objects.filter(
                business_profile=business,
                provider=email_provider,
                user=request.user,
                status=EmailAccountStatus.CONNECTED,
            )
            .order_by("-updated_at")
            .first()
        )
        if not account:
            return JsonResponse(
                {
                    "error": "EMAIL_ACCOUNT_NOT_FOUND",
                    "message": "No connected email account found for this provider.",
                },
                status=HTTPStatus.NOT_FOUND,
            )

        if request.method == "POST":
            updates = _normalize_email_tool_updates(email_provider=email_provider, payload=payload)
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
                EmailAccountAuditEvent.objects.create(
                    business_profile=business,
                    email_account=account,
                    email_account_id_snapshot=account.id,
                    actor_user=request.user if request.user.is_authenticated else None,
                    action=EmailAccountAuditAction.UPDATED,
                    description=f"{_provider_display_name(email_provider)} tool settings updated.",
                    metadata={
                        "provider": email_provider,
                        "changed_tools": len(applied),
                    },
                )

        tools_payload, summary = _serialize_email_tools_for_account(account=account)

    return JsonResponse(
        {
            "businessId": str(business.id),
            "provider": email_provider,
            "providerLabel": _provider_display_name(email_provider),
            "accountId": str(account.id),
            "accountIdentifier": str(account.email_address or ""),
            "status": str(account.status or ""),
            "summary": summary,
            "tools": tools_payload,
        },
        status=HTTPStatus.OK,
    )
