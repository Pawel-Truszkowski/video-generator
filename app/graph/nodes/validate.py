from __future__ import annotations

import os

from PIL import Image

from app.config import settings


async def validate(state: dict) -> dict:
    """Validate uploaded images: format, size, convert to RGB, resize+pad."""
    image_paths = state["image_paths"]
    if not image_paths:
        return {"error": "No images provided", "status": "error"}

    if len(image_paths) > settings.max_images:
        return {"error": f"Too many images (max {settings.max_images})", "status": "error"}

    validated_paths = []
    w, h = settings.output_width, settings.output_height

    for path in image_paths:
        size_mb = os.path.getsize(path) / (1024 * 1024)
        if size_mb > settings.max_upload_mb:
            return {"error": f"Image {os.path.basename(path)} exceeds {settings.max_upload_mb} MB", "status": "error"}

        ext = os.path.splitext(path)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            return {"error": f"Unsupported format: {ext}", "status": "error"}

        img = Image.open(path)
        img = img.convert("RGB")

        # Resize + pad to target resolution
        img.thumbnail((w, h), Image.LANCZOS)
        padded = Image.new("RGB", (w, h), (0, 0, 0))
        offset_x = (w - img.width) // 2
        offset_y = (h - img.height) // 2
        padded.paste(img, (offset_x, offset_y))

        out_path = os.path.splitext(path)[0] + "_processed.jpg"
        padded.save(out_path, "JPEG", quality=90)
        validated_paths.append(out_path)

    return {"image_paths": validated_paths, "status": "planning"}
