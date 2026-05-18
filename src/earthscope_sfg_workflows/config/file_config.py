"""Remote storage type enumeration."""

from enum import Enum


class REMOTE_TYPE(Enum):
    """Enumeration for remote storage types."""

    S3 = "s3"
    HTTP = "http"
