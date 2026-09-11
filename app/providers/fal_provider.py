from __future__ import annotations

import asyncio
import base64
import os

import fal_client
# Not re-exported from the package root in fal-client 0.5.6.
from fal_client.client import FalClientError

from app.config import settings
from app.providers.base import ProviderError


def _translate_fal_error(e: FalClientError) -> ProviderError:
    """Turn a fal.ai error into a message the user can read.

    `e.args[0]` is whatever fal put under "detail" in the error response
    (fal_client.client._raise_for_status): for a 422 a list of dicts like
        {'loc': ['body', 'image_url'], 'msg': '...',
         'type': 'content_policy_violation', 'input': {...the whole request...}}
    but a plain string for other failures, or the raw response text when the
    body was not JSON.
    """
    detail = e.args[0] if e.args else ""

    # Not a validation list: a 4xx/5xx with a plain message or a non-JSON body.
    # Usually transient, so worth another attempt. Cut last -- the string can
    # still carry an echo of the request.
    if not isinstance(detail, list):
        return ProviderError(f"Błąd fal.ai: {str(detail)[:300]}", retryable=True)

    for item in detail:
        if not isinstance(item, dict):
            return ProviderError(f"Błąd fal.ai: {str(item)[:300]}", retryable=True)

        if item.get("type") == "content_policy_violation":
            field = (item.get("loc") or ["?"])[-1]
            if field == "image_url":
                hint = (
                    "fal.ai odrzucił obraz wejściowy (filtr treści). Ponowienie "
                    "z tym samym obrazem nie pomoże — zmień obraz sceny albo "
                    "wyłącz łączenie z poprzednią sceną."
                )
            else:
                hint = (
                    f"fal.ai odrzucił pole '{field}' (filtr treści). "
                    "Zmień opis sceny i spróbuj ponownie."
                )
            return ProviderError(hint, retryable=False)

    # Any other 422: the request does not match the endpoint's schema, which
    # fails identically on every attempt. `input` is never read -- that is
    # where fal echoes the base64 image.
    first = detail[0] if detail else {}
    field = (first.get("loc") or ["?"])[-1]
    msg = str(first.get("msg") or "nieprawidłowa wartość")[:200]
    return ProviderError(f"fal.ai odrzucił pole '{field}': {msg}", retryable=False)


def _map_duration(model: str, duration_s: int) -> str:
    """Translate a planned scene duration onto the grid a model accepts.

    `durations` is the model's (seconds, api_value) list from
    settings.model_params, sorted ascending. Scenes are always planned as 5 or
    10 seconds, so models whose grid contains those values map exactly, while
    veo (4/6/8) never does and needs a rule.
    """
    durations: list[tuple[int, str]] = settings.model_params[model]["durations"]

    for sec, api_value in durations:
        if sec == duration_s:
            return api_value

    if duration_s < durations[0][0]:
        return durations[0][1]
    if duration_s > durations[-1][0]:
        return durations[-1][1]

    # Inside the range but off-grid (veo: a 5s scene, with 4 and 6 either side).
    # Nearest point wins; min() keeps the first on a tie, which is the shorter
    # one -- consistent with the clamp above, a clip is never longer than planned.
    return min(durations, key=lambda d: abs(d[0] - duration_s))[1]


def _build_arguments(
    model: str,
    data_uri: str,
    prompt: str,
    duration_s: int,
    aspect_ratio: str,
) -> dict:
    """Assemble the request body a specific fal.ai endpoint accepts.

    Only `image_url` and `prompt` are common to every endpoint; anything else
    is opt-in per model, because fal rejects undeclared fields with a 422.
    """
    params = settings.model_params.get(model)
    if params is None:
        raise ValueError(f"No request schema configured for model: {model}")

    arguments = {
        "image_url": data_uri,
        "prompt": prompt,
        "duration": _map_duration(model, duration_s),
    }
    if params["aspect_ratio"]:
        arguments["aspect_ratio"] = aspect_ratio
    arguments.update(params["extra"])
    return arguments


class FalProvider:
    """Generates video clips via fal.ai queue API."""

    async def generate_clip(
        self,
        image_path: str,
        prompt: str,
        duration_s: int,
        aspect_ratio: str,
        model: str,
        out_path: str,
    ) -> str:
        endpoint = settings.model_endpoints.get(model)
        if not endpoint:
            raise ValueError(f"Unknown model: {model}")

        # Read image and encode as data URI
        with open(image_path, "rb") as f:
            img_bytes = f.read()

        ext = os.path.splitext(image_path)[1].lower().lstrip(".")
        if ext == "jpg":
            ext = "jpeg"
        data_uri = f"data:image/{ext};base64,{base64.b64encode(img_bytes).decode()}"

        arguments = _build_arguments(model, data_uri, prompt, duration_s, aspect_ratio)

        os.environ["FAL_KEY"] = settings.fal_key

        def on_queue_update(update):
            if isinstance(update, fal_client.InProgress) and hasattr(update, "logs"):
                for log in update.logs:
                    print(f"  [fal] {log.get('message', log)}")

        try:
            result = await fal_client.subscribe_async(
                endpoint,
                arguments=arguments,
                with_logs=True,
                on_queue_update=on_queue_update,
            )
        except FalClientError as e:
            raise _translate_fal_error(e) from e

        # Download to a temporary name and move into place only once complete:
        # out_path existing is what a resume reads as "this scene is done".
        video_url = result["video"]["url"]
        tmp_path = f"{out_path}.part"

        import urllib.request
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, urllib.request.urlretrieve, video_url, tmp_path)
        os.replace(tmp_path, out_path)  # atomic within the same filesystem

        return out_path
