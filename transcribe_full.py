"""Resume-safe transcription of Radio-T 1035 using only Python's stdlib.

prepare: public MP3 download and frame-aligned chunks; no paid calls.
transcribe: process saved chunks within the agreed $0.15 total budget.
assemble: join saved texts locally; original responses remain intact.
"""

import argparse
import base64
import datetime
import hashlib
import json
import re
import urllib.error
import urllib.request
from array import array
from decimal import Decimal
from pathlib import Path

from prepare_sample import frame, synchsafe
from transcribe_sample import load_key

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
PARTS = OUT / "parts"
MODEL = "openai/whisper-large-v3-turbo"
BUDGET = Decimal("0.15")


def save(path, value):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def clock(seconds):
    value = int(seconds + 0.001)
    return f"{value // 3600:02}:{value // 60 % 60:02}:{value % 60:02}"


def prepare():
    plan_file = OUT / "full-plan.json"
    if plan_file.exists():
        print("Existing chunk plan retained.", flush=True)
        return
    source = OUT / "radio-t-1035.mp3"
    if not source.exists():
        request = urllib.request.Request(
            "https://cdn.radio-t.com/rt_podcast1035.mp3",
            headers={"User-Agent": "radio-t-txt/0.1"})
        temporary = source.with_suffix(".mp3.download")
        with urllib.request.urlopen(request, timeout=60) as response:
            expected = response.headers.get("Content-Length")
            with temporary.open("wb") as target:
                while block := response.read(1024 * 1024):
                    target.write(block)
        if expected and temporary.stat().st_size != int(expected):
            raise RuntimeError("Incomplete source download")
        temporary.replace(source)
        print("Source downloaded:", source.stat().st_size, "bytes", flush=True)
    raw = source.read_bytes()
    offset = 0
    if raw.startswith(b"ID3"):
        offset = 10 + synchsafe(raw[6:10]) + (10 if raw[5] & 0x10 else 0)
    length, frame_seconds = frame(raw, offset)
    header = raw[offset:offset + length]
    if b"Info" not in header and b"Xing" not in header:
        raise RuntimeError("Source duration header is missing")
    offset += length
    offsets = array("I")
    while offset < len(raw):
        if len(raw) - offset == 128 and raw[offset:offset + 3] == b"TAG":
            break
        length, seconds = frame(raw, offset)
        if seconds != frame_seconds or offset + length > len(raw):
            raise RuntimeError(f"Invalid frame at byte {offset}")
        offsets.append(offset)
        offset += length
    offsets.append(offset)
    frame_count = len(offsets) - 1
    duration = frame_count * frame_seconds
    metadata = read(OUT / "episode.json")
    if abs(duration - metadata["episode_seconds_from_mp3_frame_count"]) > 0.1:
        raise RuntimeError("Source duration disagrees with sample metadata")
    sample_frames = metadata["sample_frames"]
    sample_bytes = raw[offsets[0]:offsets[sample_frames]]
    if sample_bytes != (OUT / "radio-t-1035-first-2min.mp3").read_bytes():
        raise RuntimeError("Sample is not the exact prefix of this episode")
    PARTS.mkdir(exist_ok=True)
    chunks = [{"index": 0, "start_frame": 0, "end_frame": sample_frames,
               "start_seconds": 0.0, "end_seconds": sample_frames * frame_seconds,
               "duration_seconds": sample_frames * frame_seconds,
               "response": "sample-response.json", "reused_sample": True}]
    previous_end = sample_frames
    overlap_frames = round(3 / frame_seconds)
    chunk_frames = round(300 / frame_seconds)
    while previous_end < frame_count:
        start = previous_end - overlap_frames
        end = min(start + chunk_frames, frame_count)
        index = len(chunks)
        audio = PARTS / f"{index:03}.mp3"
        content = raw[offsets[start]:offsets[end]]
        audio.write_bytes(content)
        chunks.append({
            "index": index, "start_frame": start, "end_frame": end,
            "start_seconds": start * frame_seconds,
            "end_seconds": end * frame_seconds,
            "duration_seconds": (end - start) * frame_seconds,
            "audio": str(audio.relative_to(OUT)),
            "sha256": hashlib.sha256(content).hexdigest(),
            "response": f"parts/{index:03}-response.json", "reused_sample": False})
        previous_end = end
    for left, right in zip(chunks, chunks[1:]):
        assert right["start_frame"] < left["end_frame"]
        assert right["end_frame"] > left["end_frame"]
    assert chunks[-1]["end_frame"] == frame_count
    plan = {"model": MODEL, "frame_count": frame_count,
            "frame_seconds": frame_seconds, "duration_seconds": duration,
            "source_bytes": len(raw), "source_sha256": hashlib.sha256(raw).hexdigest(),
            "sample_reused": True, "overlap_seconds": overlap_frames * frame_seconds,
            "chunks": chunks}
    save(plan_file, plan)
    print(json.dumps({"chunks": len(chunks), "new_paid_requests": len(chunks) - 1,
                      "duration_seconds": duration,
                      "total_audio_seconds": sum(c["duration_seconds"] for c in chunks)}),
          flush=True)


def usage_cost(response):
    cost = response.get("usage", {}).get("cost")
    if cost is None:
        raise RuntimeError("Missing usage.cost; spending cannot be verified")
    return Decimal(str(cost))


def maximum_rate():
    with urllib.request.urlopen(
        f"https://openrouter.ai/api/v1/models/{MODEL}/endpoints", timeout=30
    ) as response:
        catalog = json.load(response)
    save(OUT / "full-preflight-endpoints.json", catalog)
    rates = []
    for endpoint in catalog["data"]["endpoints"]:
        pricing = endpoint["pricing"]
        if Decimal(str(pricing["completion"])) != 0:
            raise RuntimeError("Unexpected output pricing")
        rate = Decimal(str(pricing["prompt"]))
        if rate <= 0:
            raise RuntimeError("Unexpected audio pricing")
        rates.append(rate)
    if not rates:
        raise RuntimeError("No available endpoints")
    return max(rates)


def transcribe():
    plan = read(OUT / "full-plan.json")
    key = load_key()
    rate = maximum_rate()
    spent = Decimal(0)
    remaining = []
    for chunk in plan["chunks"]:
        response = OUT / chunk["response"]
        if response.exists():
            result = read(response)
            if not result.get("text", "").strip():
                raise RuntimeError(f"Empty response for part {chunk['index']}")
            spent += usage_cost(result)
        else:
            remaining.append(chunk)
    projected = spent + sum(Decimal(str(c["duration_seconds"])) * rate
                            for c in remaining)
    if projected > BUDGET:
        raise RuntimeError(f"Projected total ${projected} exceeds ${BUDGET}")
    print(f"Preflight: {len(remaining)} new requests, maximum total ${projected:.8f}",
          flush=True)
    for chunk in remaining:
        index = chunk["index"]
        attempt_path = PARTS / f"{index:03}-attempt.json"
        if attempt_path.exists():
            raise RuntimeError(f"Part {index} has a prior uncertain attempt; review before retry")
        audio = (OUT / chunk["audio"]).read_bytes()
        if hashlib.sha256(audio).hexdigest() != chunk["sha256"]:
            raise RuntimeError("Chunk hash mismatch")
        payload = {"model": MODEL, "input_audio": {
            "data": base64.b64encode(audio).decode("ascii"), "format": "mp3"},
            "language": "ru", "temperature": 0, "response_format": "json"}
        attempt = {"index": index, "status": "request_started", "model": MODEL,
                   "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   "maximum_estimate_usd": str(Decimal(str(chunk["duration_seconds"])) * rate),
                   "automatic_retries": False}
        save(attempt_path, attempt)
        request = urllib.request.Request(
            "https://openrouter.ai/api/v1/audio/transcriptions",
            data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=95) as response:
                raw = response.read().decode("utf-8").replace(key, "[REDACTED]")
                attempt["generation_id"] = response.headers.get("X-Generation-Id")
            (OUT / chunk["response"]).write_text(raw + "\n", encoding="utf-8")
            result = json.loads(raw)
            text = result.get("text", "").strip()
            if not text:
                raise RuntimeError("Empty transcription")
            cost = usage_cost(result)
            spent += cost
            attempt.update(status="completed", usage=result["usage"])
            save(attempt_path, attempt)
            (PARTS / f"{index:03}.txt").write_text(text + "\n", encoding="utf-8")
            print(f"Part {index}/{len(plan['chunks']) - 1}: "
                  f"{clock(chunk['start_seconds'])}–{clock(chunk['end_seconds'])}; "
                  f"{len(text)} characters; ${cost}; total ${spent}", flush=True)
            future = sum(Decimal(str(c["duration_seconds"])) * rate
                         for c in remaining if c["index"] > index)
            if spent + future > BUDGET:
                raise RuntimeError("Actual spending exceeded projection; budget review required")
        except Exception as error:
            attempt.update(status="failed_or_uncertain", error_type=type(error).__name__)
            if isinstance(error, urllib.error.HTTPError):
                attempt["http_status"] = error.code
                detail = error.read().decode("utf-8", errors="replace").replace(key, "[REDACTED]")
                (PARTS / f"{index:03}-error.txt").write_text(detail, encoding="utf-8")
            save(attempt_path, attempt)
            raise SystemExit(f"Part {index} failed ({type(error).__name__}); no automatic retry.")
    assemble()


def words(text):
    return [(m.group().lower().replace("ё", "е"), m.start(), m.end())
            for m in re.finditer(r"\w+", text)]


def join(left, right):
    """Join on an exact overlap anchor; retain both texts if uncertain."""
    a = words(left)[-50:]
    b = words(right)[:50]
    best = None
    for i in range(len(a)):
        for j in range(min(9, len(b))):
            length = 0
            while (i + length < len(a) and j + length < len(b)
                   and a[i + length][0] == b[j + length][0]):
                length += 1
            exact_short_suffix = (
                length == 2 and i + length == len(a) and j == 0
                and sum(len(w[0]) for w in a[i:]) >= 8)
            if (length >= 3 or exact_short_suffix) and len(a) - i - length <= 8:
                score = length * 3 - (len(a) - i - length) - j
                if best is None or score > best[0]:
                    best = (score, i, j, length)
    if best is None:
        return left.rstrip() + "\n\n" + right.lstrip(), {"method": "kept_both"}
    _, i, j, length = best
    left_end = a[i + length - 1][2]
    right_end = b[j + length - 1][2]
    combined = left[:left_end] + right[right_end:]
    return combined, {"method": "exact_anchor", "anchor_words": length,
                      "anchor": " ".join(w[0] for w in a[i:i + length]),
                      "discarded_left_tail": left[left_end:],
                      "discarded_right_prefix": right[:b[j][1]]}


def assemble():
    plan = read(OUT / "full-plan.json")
    override_path = OUT / "stitch-overrides.json"
    overrides = read(override_path) if override_path.exists() else {}
    merged = ""
    raw_sections = []
    merges = []
    spent = Decimal(0)
    records = []
    for chunk in plan["chunks"]:
        result = read(OUT / chunk["response"])
        text = re.sub(r"\s+", " ", result["text"]).strip()
        if not text:
            raise RuntimeError("Cannot assemble an empty part")
        spent += usage_cost(result)
        label = f"{clock(chunk['start_seconds'])}–{clock(chunk['end_seconds'])}"
        raw_sections.append(f"## {label}\n\n{text}")
        if merged:
            override = overrides.get(str(chunk["index"]))
            if override:
                suffix = override["left_suffix"]
                prefix = override["right_prefix"]
                if not merged.endswith(suffix) or not text.startswith(prefix):
                    raise RuntimeError("Reviewed overlap no longer matches the source texts")
                previous = merged[:-len(suffix)] if suffix else merged
                following = text[len(prefix):]
                merged = previous.rstrip() + " " + following.lstrip()
                report = {"method": "reviewed_overlap", **override}
            else:
                merged, report = join(merged, text)
            merges.append({"part": chunk["index"], **report})
        else:
            merged = text
        records.append({"part": chunk["index"], "range": label,
                        "characters": len(text), "usage": result["usage"]})
    if spent > BUDGET:
        raise RuntimeError("Total cost exceeds approved budget")
    # Add readable paragraphs using only whitespace changes.
    sentences = re.split(r'(?<=[.!?…])\s+(?=[А-ЯЁA-Z«])', merged.strip())
    paragraphs = []
    paragraph = ""
    for sentence in sentences:
        paragraph = (paragraph + " " + sentence).strip()
        if len(paragraph) >= 650:
            paragraphs.append(paragraph)
            paragraph = ""
    if paragraph:
        paragraphs.append(paragraph)
    formatted = "\n\n".join(paragraphs)
    assert re.sub(r"\s+", " ", formatted).strip() == re.sub(r"\s+", " ", merged).strip()
    (OUT / "radio-t-1035.txt").write_text(formatted + "\n", encoding="utf-8")
    (OUT / "radio-t-1035-by-parts.md").write_text(
        "# Радио-Т 1035 — 24 октября 2026\n\n"
        "Исходная расшифровка по фрагментам. Соседние фрагменты перекрываются на три секунды.\n\n"
        + "\n\n".join(raw_sections) + "\n", encoding="utf-8")
    report = {"status": "completed", "model": MODEL,
              "episode_duration_seconds": plan["duration_seconds"],
              "chunks_completed": len(records), "sample_reused": True,
              "total_cost_usd": str(spent), "budget_usd": str(BUDGET),
              "text_characters": len(merged), "text_words": len(words(merged)),
              "joins_needing_review": [m["part"] for m in merges if m["method"] == "kept_both"],
              "chunks": records, "joins": merges}
    save(OUT / "full-report.json", report)
    print(json.dumps({k: v for k, v in report.items() if k not in ("chunks", "joins")},
                     ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "transcribe", "assemble"))
    action = parser.parse_args().action
    {"prepare": prepare, "transcribe": transcribe, "assemble": assemble}[action]()
