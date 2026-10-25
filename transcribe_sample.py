"""Transcribe the prepared two-minute sample once, within the agreed budget."""

import base64
import datetime
import json
import urllib.error
import urllib.request
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
MODEL = "openai/whisper-large-v3-turbo"
LIMIT = Decimal("0.002")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def load_key():
    for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].strip()
        name, sep, value = line.partition("=")
        if sep and name.strip() == "OPENROUTER_API_KEY":
            value = value.strip()
            if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
                value = value[1:-1]
            if not value or "\n" in value or "\r" in value:
                raise RuntimeError("OPENROUTER_API_KEY is empty or invalid")
            return value
    raise RuntimeError("OPENROUTER_API_KEY is missing from .env")


def main():
    response_file = OUT / "sample-response.json"
    attempt_file = OUT / "sample-attempt.json"
    if response_file.exists():
        print("Saved response already exists; no paid request made.")
        return
    if attempt_file.exists():
        raise RuntimeError("A previous attempt is recorded. Review it before retrying.")
    key = load_key()
    metadata = json.loads((OUT / "episode.json").read_text())
    audio = OUT / "radio-t-1035-first-2min.mp3"
    if audio.stat().st_size != metadata["sample_bytes"]:
        raise RuntimeError("Sample size no longer matches its metadata")
    with urllib.request.urlopen(
        f"https://openrouter.ai/api/v1/models/{MODEL}/endpoints", timeout=30
    ) as response:
        catalog = json.load(response)
    endpoints = catalog["data"]["endpoints"]
    if not endpoints:
        raise RuntimeError("No transcription endpoints available")
    rates = []
    for endpoint in endpoints:
        pricing = endpoint["pricing"]
        if Decimal(str(pricing["completion"])) != 0:
            raise RuntimeError("Unexpected output pricing; budget must be reviewed")
        rate = Decimal(str(pricing["prompt"]))
        if rate <= 0:
            raise RuntimeError("Unexpected audio pricing; budget must be reviewed")
        rates.append(rate)
    estimate = Decimal(str(metadata["sample_seconds"])) * max(rates)
    if estimate > LIMIT:
        raise RuntimeError(f"Estimated maximum ${estimate} exceeds ${LIMIT}")
    write_json(OUT / "sample-preflight-endpoints.json", catalog)
    payload = {
        "model": MODEL,
        "input_audio": {
            "data": base64.b64encode(audio.read_bytes()).decode("ascii"),
            "format": "mp3",
        },
        "language": "ru",
        "temperature": 0,
        "response_format": "json",
    }
    attempt = {
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "model": MODEL,
        "budget_usd": str(LIMIT),
        "maximum_catalog_estimate_usd": str(estimate),
        "status": "request_started",
        "automatic_retries": False,
    }
    # Persist before POST so a timeout cannot silently cause duplicate billing.
    write_json(attempt_file, attempt)
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/audio/transcriptions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=95) as response:
            raw = response.read().decode("utf-8").replace(key, "[REDACTED]")
            attempt["generation_id"] = response.headers.get("X-Generation-Id")
        # Preserve the response even if its format is unexpected.
        response_file.write_text(raw + "\n", encoding="utf-8")
        result = json.loads(raw)
        text = result.get("text")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("API response contains no transcription text")
        (OUT / "sample.txt").write_text(text.strip() + "\n", encoding="utf-8")
        attempt["status"] = "completed"
        attempt["usage"] = result.get("usage")
        write_json(attempt_file, attempt)
        print(json.dumps({"status": "completed", "usage": result.get("usage"),
                          "text_file": str(OUT / "sample.txt")}, ensure_ascii=False))
    except Exception as error:
        attempt["status"] = "failed_or_uncertain"
        attempt["error_type"] = type(error).__name__
        if isinstance(error, urllib.error.HTTPError):
            attempt["http_status"] = error.code
            detail = error.read().decode("utf-8", errors="replace")
            detail = detail.replace(key, "[REDACTED]")
            (OUT / "sample-error.txt").write_text(detail, encoding="utf-8")
        write_json(attempt_file, attempt)
        # Avoid printing exception contents, which may include upstream data.
        raise SystemExit(f"Request failed ({type(error).__name__}); no automatic retry.")


if __name__ == "__main__":
    main()
