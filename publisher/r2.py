"""Subida de slides a Cloudflare R2 para darles una URL publica temporal.

Meta hace `cURL` de las imagenes al crear el contenedor de media, asi que
necesitan estar en una URL publica en ese momento. R2 es lo que permite que el
repo del cliente sea 100% privado: solo las slides salen a internet, y solo
mientras se publica.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import boto3
from botocore.config import Config


class R2NotConfigured(RuntimeError):
    pass


@dataclass
class R2:
    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket: str
    public_base: str  # p.ej. https://media.invininversiones.es (sin barra final)

    @classmethod
    def from_env(cls) -> "R2":
        missing = [
            name
            for name in (
                "R2_ACCOUNT_ID",
                "R2_ACCESS_KEY_ID",
                "R2_SECRET_ACCESS_KEY",
                "R2_BUCKET",
                "R2_PUBLIC_URL",
            )
            if not os.environ.get(name)
        ]
        if missing:
            raise R2NotConfigured("faltan variables de entorno: " + ", ".join(missing))
        return cls(
            account_id=os.environ["R2_ACCOUNT_ID"],
            access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            bucket=os.environ["R2_BUCKET"],
            public_base=os.environ["R2_PUBLIC_URL"].rstrip("/"),
        )

    def _client(self):
        return boto3.client(
            "s3",
            endpoint_url=f"https://{self.account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            # R2 no implementa regiones, pero boto3 exige uma. "auto" es lo que
            # documenta Cloudflare.
            region_name="auto",
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        )

    def upload_slides(self, client_prefix: str, slug: str, slides: list[Path]) -> list[str]:
        """Sube las slides y devuelve sus URLs publicas, en el mismo orden."""
        s3 = self._client()
        urls = []
        for slide in slides:
            key = f"{client_prefix}/{slug}/{slide.name}"
            s3.upload_file(
                str(slide),
                self.bucket,
                key,
                ExtraArgs={"ContentType": "image/jpeg"},
            )
            urls.append(f"{self.public_base}/{key}")
        return urls
