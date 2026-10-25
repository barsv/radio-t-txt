"""Download the first two minutes of Radio-T 1035 without re-encoding.

Public downloads only; no API key or paid requests. Python standard library.
"""

import json
import urllib.request
from pathlib import Path


EPISODE = 1035
MODEL = "openai/whisper-large-v3-turbo"
ROOT = Path(__file__).resolve().parent


def fetch(url, byte_range=None):
    headers = {"User-Agent": "radio-t-txt/0.1"}
    if byte_range:
        headers["Range"] = byte_range
    with urllib.request.urlopen(
        urllib.request.Request(url, headers=headers), timeout=45
    ) as response:
        if byte_range and response.status != 206:
            raise RuntimeError("Server did not honor the range request")
        return response.read()


def synchsafe(data):
    value = 0
    for byte in data:
        if byte & 128:
            raise ValueError("Invalid ID3 synchsafe integer")
        value = (value << 7) | byte
    return value


def frame(data, offset):
    if offset + 4 > len(data):
        raise ValueError("Missing MP3 header")
    h = int.from_bytes(data[offset:offset + 4], "big")
    version = (h >> 19) & 3
    bitrate_index = (h >> 12) & 15
    rate_index = (h >> 10) & 3
    if (h >> 21 != 0x7FF or version == 1 or (h >> 17) & 3 != 1
            or bitrate_index in (0, 15) or rate_index == 3):
        raise ValueError("Not a supported MPEG Layer III frame")
    rates = (44100, 48000, 32000)
    rate = rates[rate_index] // {3: 1, 2: 2, 0: 4}[version]
    bitrates = ((0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192,
                 224, 256, 320) if version == 3 else
                (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112,
                 128, 144, 160))
    bitrate = bitrates[bitrate_index] * 1000
    samples = 1152 if version == 3 else 576
    length = (144 if version == 3 else 72) * bitrate // rate + ((h >> 9) & 1)
    return length, samples / rate


def main():
    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    raw = fetch(f"https://cdn.radio-t.com/rt_podcast{EPISODE}.mp3",
                "bytes=0-2200000")
    offset = 0
    if raw.startswith(b"ID3"):
        offset = 10 + synchsafe(raw[6:10])
        if raw[5] & 0x10:
            offset += 10
    length, seconds = frame(raw, offset)
    first = raw[offset:offset + length]
    # Omit the source Info/Xing frame: its length describes the entire episode.
    marker = max(first.find(b"Info"), first.find(b"Xing"))
    if marker < 0:
        raise RuntimeError("Expected a source Info/Xing duration header")
    flags = int.from_bytes(first[marker + 4:marker + 8], "big")
    if not flags & 1:
        raise RuntimeError("Source does not report its frame count")
    count = int.from_bytes(first[marker + 8:marker + 12], "big")
    episode_seconds = count * seconds
    offset += length
    frames = []
    duration = 0.0
    while duration < 120:
        length, seconds = frame(raw, offset)
        if offset + length > len(raw):
            raise RuntimeError("Downloaded prefix is too short")
        frames.append(raw[offset:offset + length])
        offset += length
        duration += seconds
    clip = output / f"radio-t-{EPISODE}-first-2min.mp3"
    clip.write_bytes(b"".join(frames))
    catalog = json.loads(fetch(
        f"https://openrouter.ai/api/v1/models/{MODEL}/endpoints"))
    (output / "model-endpoints.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n")
    prices = []
    for endpoint in catalog["data"]["endpoints"]:
        rate = float(endpoint["pricing"]["prompt"])
        prices.append({"provider": endpoint["provider_name"],
                       "usd_per_second": rate,
                       "sample_usd": duration * rate,
                       "episode_usd": episode_seconds * rate})
    metadata = {
        "episode": EPISODE,
        "date": "2026-10-24",
        "page": "https://radio-t.com/p/2026/10/24/podcast-1035/",
        "audio": f"https://cdn.radio-t.com/rt_podcast{EPISODE}.mp3",
        "episode_seconds_from_mp3_frame_count": episode_seconds,
        "sample_seconds": duration,
        "sample_frames": len(frames),
        "sample_bytes": clip.stat().st_size,
        "sample_method": "Original MP3 frames, no re-encoding; source tags omitted",
        "model": MODEL,
        "provider_prices_usd": prices,
        "paid_requests_made": 0,
    }
    (output / "episode.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    print("Sample:", clip)


if __name__ == "__main__":
    main()
