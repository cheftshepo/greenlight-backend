"""
Azure Blob Storage Utilities
Location: function_app_pkg/core/storage.py

FIXED: ContentSettings object (not dict) passed to upload_blob.
       Added generate_sas_url() used by download endpoints.
"""
import logging
import os
from datetime import datetime, timedelta
from typing import Optional, Tuple

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import (
    BlobServiceClient,
    BlobSasPermissions,
    ContentSettings,
    generate_blob_sas,
)

logger = logging.getLogger(__name__)


class BlobStorageManager:
    """Manage Azure Blob Storage operations — lazy-initialized singleton."""

    def __init__(self):
        self._client: Optional[BlobServiceClient] = None
        self._initialized = False
        self.documents_container = os.getenv("AZURE_STORAGE_CONTAINER", "documents")

    # ------------------------------------------------------------------
    # INIT
    # ------------------------------------------------------------------

    def _ensure_initialized(self):
        """Initialize on first use, NOT at import time."""
        if self._initialized:
            return

        conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        if not conn_str:
            raise ValueError(
                "AZURE_STORAGE_CONNECTION_STRING is not set. "
                "Add it in Azure Portal → Function App → Configuration → Application settings."
            )

        self._client = BlobServiceClient.from_connection_string(conn_str)
        self._ensure_container_exists()
        self._initialized = True
        logger.info("✅ BlobStorageManager initialized")

    @property
    def client(self) -> BlobServiceClient:
        self._ensure_initialized()
        return self._client

    def _ensure_container_exists(self):
        """Create the documents container if it doesn't already exist."""
        try:
            self._client.create_container(self.documents_container)
            logger.info(f"✅ Created container: {self.documents_container}")
        except Exception as e:
            if "ContainerAlreadyExists" not in str(e):
                logger.warning(f"⚠️ Container check: {e}")

    # ------------------------------------------------------------------
    # UPLOAD
    # ------------------------------------------------------------------

    def upload_file(
        self,
        file_content: bytes,
        blob_path: str,
        content_type: str = "application/octet-stream",
        metadata: Optional[dict] = None,
    ) -> Tuple[str, str]:
        """
        Upload bytes to blob storage.

        Returns:
            (blob_url, blob_path)

        Raises:
            Exception on upload failure — caller should handle.
        """
        self._ensure_initialized()

        blob_client = self.client.get_blob_client(
            container=self.documents_container,
            blob=blob_path,
        )

        # ContentSettings MUST be an object — passing a dict causes:
        # AttributeError: 'dict' object has no attribute 'cache_control'
        blob_client.upload_blob(
            file_content,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
            metadata={str(k): str(v) for k, v in (metadata or {}).items()},
        )

        blob_url = blob_client.url
        logger.info(f"✅ Uploaded: {blob_path} ({len(file_content):,} bytes)")
        return blob_url, blob_path

    # ------------------------------------------------------------------
    # DOWNLOAD
    # ------------------------------------------------------------------

    def download_file(self, blob_path: str) -> Tuple[bytes, str]:
        """
        Download file bytes from blob storage.

        Returns:
            (file_content, content_type)

        Raises:
            FileNotFoundError if blob does not exist.
        """
        self._ensure_initialized()

        try:
            blob_client = self.client.get_blob_client(
                container=self.documents_container,
                blob=blob_path,
            )
            stream = blob_client.download_blob()
            file_content = stream.readall()
            props = blob_client.get_blob_properties()
            content_type = (
                props.content_settings.content_type or "application/octet-stream"
            )
            logger.info(f"✅ Downloaded: {blob_path} ({len(file_content):,} bytes)")
            return file_content, content_type

        except ResourceNotFoundError:
            logger.error(f"❌ Not found: {blob_path}")
            raise FileNotFoundError(f"File not found in storage: {blob_path}")

        except Exception as e:
            logger.error(f"❌ Download failed [{blob_path}]: {e}")
            raise

    # ------------------------------------------------------------------
    # SAS URL  (used by /file and /download-corrected endpoints)
    # ------------------------------------------------------------------

    def generate_sas_url(
        self,
        blob_path: str,
        expiry_hours: int = 1,
        disposition: Optional[str] = None,
    ) -> Optional[str]:
        """
        Generate a time-limited SAS URL for direct browser download.

        Returns None if AZURE_STORAGE_ACCOUNT_KEY is not configured
        (caller falls back to streaming through the backend).
        """
        account_key = os.getenv("AZURE_STORAGE_ACCOUNT_KEY")
        if not account_key:
            logger.debug("No AZURE_STORAGE_ACCOUNT_KEY — SAS URL unavailable")
            return None

        self._ensure_initialized()

        try:
            account_name = self.client.account_name

            sas_kwargs = dict(
                account_name=account_name,
                container_name=self.documents_container,
                blob_name=blob_path,
                account_key=account_key,
                permission=BlobSasPermissions(read=True),
                expiry=datetime.utcnow() + timedelta(hours=expiry_hours),
            )
            if disposition:
                sas_kwargs["content_disposition"] = disposition

            token = generate_blob_sas(**sas_kwargs)
            url = (
                f"https://{account_name}.blob.core.windows.net"
                f"/{self.documents_container}/{blob_path}?{token}"
            )
            logger.info(f"✅ SAS URL generated for: {blob_path}")
            return url

        except Exception as e:
            logger.warning(f"⚠️ SAS URL generation failed: {e}")
            return None

    # ------------------------------------------------------------------
    # DELETE
    # ------------------------------------------------------------------

    def delete_file(self, blob_path: str) -> bool:
        """Delete a blob. Returns True if deleted, False if not found."""
        self._ensure_initialized()
        try:
            self.client.get_blob_client(
                container=self.documents_container,
                blob=blob_path,
            ).delete_blob()
            logger.info(f"✅ Deleted: {blob_path}")
            return True
        except ResourceNotFoundError:
            logger.warning(f"⚠️ Not found for deletion: {blob_path}")
            return False
        except Exception as e:
            logger.error(f"❌ Delete failed [{blob_path}]: {e}")
            return False

    # ------------------------------------------------------------------
    # EXISTS
    # ------------------------------------------------------------------

    def file_exists(self, blob_path: str) -> bool:
        """Check whether a blob exists without downloading it."""
        self._ensure_initialized()
        try:
            return self.client.get_blob_client(
                container=self.documents_container,
                blob=blob_path,
            ).exists()
        except Exception:
            return False


# Singleton — safe at import time because initialisation is lazy.
blob_storage = BlobStorageManager()