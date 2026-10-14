"""Call the hosted annotator and independently check its returned source."""

from contextlib import suppress
import os
from urllib.parse import urlparse

from .annotate import collect_blocks, parse_python, semantic_ast_dump
from .core import AnnotationResult
from .java import collect_java_blocks, java_syntax_fingerprint, parse_java
from .model import ModelUnavailableError


MODEL_LABELS = {"base": "Base Mellum2", "tuned": "LoRA adapter (experimental)"}
ANNOTATION_TIMEOUT = 180


def _request_annotation(source: str, model_choice: str, language: str, url: str):
    # Importing the client here keeps the local Git command dependency-light.
    from gradio_client import Client

    host = urlparse(url).hostname
    if not host or (
        urlparse(url).scheme != "https" and host not in {"127.0.0.1", "localhost"}
    ):
        raise ModelUnavailableError(
            "BTL_REMOTE_URL must be an HTTPS annotation service URL."
        )
    if host == os.getenv("SPACE_HOST"):
        raise ModelUnavailableError(
            "The annotation service cannot call itself. Remove BTL_REMOTE_URL from the backend Space."
        )

    client = None
    job = None
    try:
        # A direct URL avoids the client's unbounded wait for a building Space.
        client = Client(
            url,
            hf_token=os.getenv("HF_TOKEN"),
            verbose=False,
            httpx_kwargs={"timeout": 30},
        )
        endpoints = client.config.get("dependencies", [])
        if not any(
            endpoint.get("api_name") == "annotate_multilang" for endpoint in endpoints
        ):
            raise ModelUnavailableError(
                "Update the Hugging Face Space to the Python and Java version before connecting this app."
            )
        job = client.submit(
            source,
            MODEL_LABELS[model_choice],
            language.title(),
            api_name="/annotate_multilang",
        )
        return job.result(timeout=ANNOTATION_TIMEOUT)
    except ModelUnavailableError:
        raise
    except TimeoutError as exc:
        if job is not None:
            with suppress(Exception):
                job.cancel()
        raise ModelUnavailableError(
            "Annotation timed out. The hosted model may be starting or busy; try again later."
        ) from exc
    except Exception as exc:
        raise ModelUnavailableError(
            "Cannot reach the hosted annotator. Check that the Hugging Face Space is running."
        ) from exc
    finally:
        if client is not None:
            with suppress(Exception):
                client.close()


def _check_comment_additions(source: str, annotated: str, language: str) -> None:
    """Require every original line, in order, with only full-line comments added."""
    original_lines = source.splitlines(keepends=True)
    position = 0
    prefix = "#" if language == "python" else "//"
    for line in annotated.splitlines(keepends=True):
        if position < len(original_lines) and line == original_lines[position]:
            position += 1
        elif not line.lstrip().startswith(prefix):
            raise ValueError("The hosted response changed an original source line.")
        elif language == "java" and "\\u" in line:
            # Java expands Unicode escapes even inside comments.
            raise ValueError("The hosted response added a Java Unicode escape.")
    if position != len(original_lines):
        raise ValueError("The hosted response removed an original source line.")


def annotate_remote(
    source: str, model_choice: str, language: str, url: str
) -> AnnotationResult:
    if language not in {"python", "java"}:
        return AnnotationResult(
            "", source, f"Unsupported language: {language}", False, 0
        )
    if not source.strip():
        return AnnotationResult(
            "", source, f"Paste a {language.title()} file to annotate.", False, 0
        )
    if model_choice not in MODEL_LABELS or (
        language == "java" and model_choice == "tuned"
    ):
        return AnnotationResult(
            "",
            source,
            "The LoRA adapter supports Python only. Select the base model for Java.",
            False,
            0,
        )

    fingerprint = semantic_ast_dump if language == "python" else java_syntax_fingerprint
    try:
        before = fingerprint(source)
        blocks = (
            collect_blocks(parse_python(source), source)
            if language == "python"
            else collect_java_blocks(parse_java(source), source)
        )
    except SyntaxError as exc:
        return AnnotationResult("", source, f"Syntax error: {exc}", False, 0)

    data = _request_annotation(source, model_choice, language, url)
    if (
        not isinstance(data, (tuple, list))
        or len(data) != 3
        or not all(isinstance(value, str) for value in data)
    ):
        return AnnotationResult(
            "",
            source,
            "The hosted annotator returned an invalid response.",
            False,
            len(blocks),
        )
    summary, annotated, status = data
    if not status.startswith(("Validated:", "Parsed successfully.")):
        return AnnotationResult(
            "",
            source,
            status or "The hosted annotator could not complete this request.",
            False,
            len(blocks),
        )
    try:
        _check_comment_additions(source, annotated, language)
        if before != fingerprint(annotated):
            raise ValueError("The hosted response changed the parsed code.")
    except (SyntaxError, ValueError) as exc:
        return AnnotationResult("", source, f"Rejected: {exc}", False, len(blocks))
    return AnnotationResult(summary, annotated, status, True, len(blocks))
