from __future__ import annotations

from .provider_shared import *  # noqa: F403


@csrf_protect
@require_http_methods(["GET"])
def voice_providers_collection(request: HttpRequest) -> JsonResponse:
    business_param = request.GET.get("business_id") or request.GET.get("businessId")
    business, error = _resolve_business_for_request(request, business_param)
    if error:
        return error
    assert business is not None

    with tenant_context(business.id):
        connections = {
            conn.provider: conn
            for conn in VoiceProviderConnection.objects.filter(
                business_profile=business,
                provider__in=VOICE_PROVIDER_TENANT_MANAGED,
            ).order_by("provider")
        }
        active_provider = _sync_active_transport_provider(business=business)
        providers = [
            _serialize_provider(
                provider=provider,
                connection=connections.get(provider),
                active_transport_provider=active_provider,
            )
            for provider in VOICE_PROVIDER_ORDER
        ]

    return JsonResponse(
        {
            "businessId": str(business.id),
            "dashboardUrl": "/dashboard/voice/",
            "activeTransportProvider": active_provider or None,
            "providers": providers,
        },
        status=HTTPStatus.OK,
    )


@csrf_protect
@require_http_methods(["GET", "PUT", "DELETE"])
def voice_provider_detail(request: HttpRequest, provider: str) -> JsonResponse:
    provider_key = _normalize_provider(provider)
    if not _is_supported_provider(provider_key):
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "Unsupported voice provider."},
            status=HTTPStatus.BAD_REQUEST,
        )

    payload: dict[str, Any] | None = None
    business_param = request.GET.get("business_id") or request.GET.get("businessId")
    if request.method in {"PUT", "DELETE"}:
        payload, parse_error = _parse_json_body(request)
        if parse_error:
            return parse_error
        business_param = (
            (payload or {}).get("businessId")
            or (payload or {}).get("business_id")
            or business_param
        )

    business, error = _resolve_business_for_request(request, business_param)
    if error:
        return error
    assert business is not None

    if request.method in {"PUT", "DELETE"} and not is_tenant_managed_provider(provider_key):
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": PLATFORM_MANAGED_MESSAGE},
            status=HTTPStatus.BAD_REQUEST,
        )

    with tenant_context(business.id):
        connection = VoiceProviderConnection.objects.filter(
            business_profile=business,
            provider=provider_key,
        ).first()
        active_provider = _sync_active_transport_provider(business=business)

        if request.method == "GET":
            return JsonResponse(
                {
                    "activeTransportProvider": active_provider or None,
                    "provider": _serialize_provider(
                        provider=provider_key,
                        connection=connection,
                        active_transport_provider=active_provider,
                    ),
                },
                status=HTTPStatus.OK,
            )

        if request.method == "DELETE":
            if connection:
                connection.enabled = False
                connection.clear_credentials()
                connection.last_error = ""
                connection.save(
                    update_fields=[
                        "enabled",
                        "credentials_encrypted",
                        "credentials_key_version",
                        "credentials_last_rotated_at",
                        "credential_error_count",
                        "last_error",
                        "updated_at",
                    ]
                )
            active_provider = _sync_active_transport_provider(
                business=business,
                cleared_provider=provider_key,
            )
            return JsonResponse(
                {"activeTransportProvider": active_provider or None},
                status=HTTPStatus.OK,
            )

        assert payload is not None
        if connection is None:
            connection = VoiceProviderConnection(
                business_profile=business,
                provider=provider_key,
                created_by=request.user,
            )

        credentials_payload = payload.get("credentials")
        if credentials_payload is not None and not isinstance(credentials_payload, dict):
            return JsonResponse(
                {"error": "VALIDATION_ERROR", "message": "credentials must be a JSON object."},
                status=HTTPStatus.BAD_REQUEST,
            )

        credentials_supplied = isinstance(credentials_payload, dict)
        clear_credentials = bool(payload.get("clearCredentials"))

        current_credentials = connection.credentials if connection.pk else {}
        next_credentials = dict(current_credentials or {})
        if clear_credentials:
            next_credentials = {}
        elif credentials_supplied:
            next_credentials = _merge_credentials(next_credentials, credentials_payload or {})

        normalized_credentials, normalize_error = _normalize_credentials(
            provider_key,
            next_credentials,
        )
        if normalize_error:
            return JsonResponse(
                {"error": "VALIDATION_ERROR", "message": normalize_error},
                status=HTTPStatus.BAD_REQUEST,
            )

        enabled_payload = payload.get("enabled")
        if enabled_payload is None:
            next_enabled = connection.enabled if connection.pk else bool(normalized_credentials)
        else:
            next_enabled = bool(enabled_payload)

        if next_enabled:
            missing = _missing_required(provider_key, normalized_credentials)
            if missing:
                return JsonResponse(
                    {
                        "error": "VALIDATION_ERROR",
                        "message": f"Missing required fields for {VOICE_PROVIDER_LABELS.get(provider_key, provider_key)}: {', '.join(missing)}",
                    },
                    status=HTTPStatus.BAD_REQUEST,
                )

        if credentials_supplied or clear_credentials:
            connection.credentials = normalized_credentials
        connection.enabled = next_enabled
        connection.last_error = ""
        connection.save()

        set_as_active = bool(payload.get("setAsActive"))
        active_provider = _sync_active_transport_provider(
            business=business,
            preferred_provider=provider_key if (set_as_active and next_enabled) else None,
            cleared_provider=provider_key if not next_enabled else None,
        )

        return JsonResponse(
            {
                "activeTransportProvider": active_provider or None,
                "provider": _serialize_provider(
                    provider=provider_key,
                    connection=connection,
                    active_transport_provider=active_provider,
                ),
            },
            status=HTTPStatus.OK,
        )


@csrf_protect
@require_http_methods(["POST"])
def voice_provider_test(request: HttpRequest, provider: str) -> JsonResponse:
    provider_key = _normalize_provider(provider)
    if not _is_supported_provider(provider_key):
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": "Unsupported voice provider."},
            status=HTTPStatus.BAD_REQUEST,
        )

    payload, parse_error = _parse_json_body(request)
    if parse_error:
        return parse_error

    business_param = (
        (payload or {}).get("businessId")
        or (payload or {}).get("business_id")
        or request.GET.get("business_id")
        or request.GET.get("businessId")
    )
    business, error = _resolve_business_for_request(request, business_param)
    if error:
        return error
    assert business is not None

    if not is_tenant_managed_provider(provider_key):
        return JsonResponse(
            {"error": "VALIDATION_ERROR", "message": PLATFORM_MANAGED_MESSAGE},
            status=HTTPStatus.BAD_REQUEST,
        )

    with tenant_context(business.id):
        connection = VoiceProviderConnection.objects.filter(
            business_profile=business,
            provider=provider_key,
        ).first()
        if not connection or not connection.has_credentials():
            return JsonResponse(
                {"error": "NOT_CONFIGURED", "message": "Provider credentials are not configured yet."},
                status=HTTPStatus.BAD_REQUEST,
            )

        credentials = connection.credentials or {}

        try:
            ok, message = _run_provider_test(provider_key, credentials)
        except Exception as exc:
            ok = False
            message = str(exc) or "Provider test failed."

        connection.last_tested_at = timezone.now()
        connection.last_error = "" if ok else message
        connection.save(update_fields=["last_tested_at", "last_error", "updated_at"])

        response_payload = {
            "ok": ok,
            "message": message,
            "provider": _serialize_provider(
                provider=provider_key,
                connection=connection,
                active_transport_provider=_sync_active_transport_provider(business=business),
            ),
        }
        status_code = HTTPStatus.OK if ok else HTTPStatus.BAD_GATEWAY
        return JsonResponse(response_payload, status=status_code)
