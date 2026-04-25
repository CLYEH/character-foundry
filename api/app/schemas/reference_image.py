"""DTO for the reference-image upload response.

Mirrors api-shape.md §5.2:
    POST /v1/creation-sessions/{id}/reference-images
    201: { reference_image_id, url }

`url` is a signed URL produced by the storage backend — short TTL, only
the upload's owner can fetch it. Frontend uses it for the upload preview
and as the `<img src>` while the user iterates on prompts.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel


class ReferenceImageUploadResponse(BaseModel):
    reference_image_id: uuid.UUID
    url: str
