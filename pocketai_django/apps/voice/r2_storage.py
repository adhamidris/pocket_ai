from __future__ import annotations

from dataclasses import dataclass

import boto3
from botocore.config import Config
from django.conf import settings


@dataclass(frozen=True)
class R2Config:
    endpoint_url: str
    region: str
    bucket: str
    access_key_id: str
    secret_access_key: str


def load_r2_config() -> R2Config | None:
    endpoint_url = str(getattr(settings, "VOICE_R2_ENDPOINT_URL", "") or "").strip()
    bucket = str(getattr(settings, "VOICE_R2_BUCKET", "") or "").strip()
    access_key_id = str(getattr(settings, "VOICE_R2_ACCESS_KEY_ID", "") or "").strip()
    secret_access_key = str(getattr(settings, "VOICE_R2_SECRET_ACCESS_KEY", "") or "").strip()
    region = str(getattr(settings, "VOICE_R2_REGION", "") or "auto").strip()
    if not (endpoint_url and bucket and access_key_id and secret_access_key):
        return None
    return R2Config(
        endpoint_url=endpoint_url,
        region=region,
        bucket=bucket,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )


def build_r2_client(cfg: R2Config):
    return boto3.client(
        "s3",
        endpoint_url=cfg.endpoint_url,
        region_name=cfg.region,
        aws_access_key_id=cfg.access_key_id,
        aws_secret_access_key=cfg.secret_access_key,
        config=Config(signature_version="s3v4"),
    )

