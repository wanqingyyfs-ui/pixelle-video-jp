from __future__ import annotations

import json
import random
import re
import shutil
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Sequence


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}

_ILLEGAL_WIN_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def is_image_asset(path: str) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def is_video_asset(path: str) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


def split_assets_by_type(asset_paths: Sequence[str]) -> tuple[list[str], list[str]]:
    image_assets: list[str] = []
    video_assets: list[str] = []

    for asset_path in asset_paths or []:
        if is_image_asset(asset_path):
            image_assets.append(asset_path)
        elif is_video_asset(asset_path):
            video_assets.append(asset_path)

    return image_assets, video_assets


def validate_batch_selection(
    image_assets: Sequence[str],
    video_assets: Sequence[str],
    image_count: int,
    video_count: int,
    batch_count: int,
) -> None:
    image_count = int(image_count or 0)
    video_count = int(video_count or 0)
    batch_count = int(batch_count or 0)

    if batch_count < 1 or batch_count > 10:
        raise ValueError("Video batch count must be between 1 and 10.")

    if image_count < 0 or video_count < 0:
        raise ValueError("Random asset counts cannot be negative.")

    if image_count == 0 and video_count == 0:
        raise ValueError("Select at least one image or one video for each generated video.")

    if len(image_assets) < image_count:
        raise ValueError(
            f"Not enough image assets: uploaded {len(image_assets)}, requested {image_count}. "
            "Please upload more image assets or reduce the random image count."
        )

    if len(video_assets) < video_count:
        raise ValueError(
            f"Not enough video assets: uploaded {len(video_assets)}, requested {video_count}. "
            "Please upload more video assets or reduce the random video count."
        )


def build_batch_id(now: datetime | None = None) -> str:
    current = now or datetime.now()
    return current.strftime("batch_%Y%m%d_%H%M%S")


def make_batch_rng(seed: str | None = None) -> random.Random:
    return random.Random(seed or datetime.now().isoformat())


def material_overlap_ratio(left_assets: Sequence[str], right_assets: Sequence[str]) -> float:
    left = set(left_assets or [])
    right = set(right_assets or [])

    if not left and not right:
        return 0.0

    union = left | right
    if not union:
        return 0.0

    return len(left & right) / len(union)


def max_material_overlap(candidate_assets: Sequence[str], previous_asset_sets: Sequence[Sequence[str]]) -> float:
    if not previous_asset_sets:
        return 0.0

    return max(
        material_overlap_ratio(candidate_assets, previous_assets)
        for previous_assets in previous_asset_sets
    )


def select_batch_assets(
    image_assets: Sequence[str],
    video_assets: Sequence[str],
    image_count: int,
    video_count: int,
    rng: random.Random,
) -> tuple[list[str], list[str], list[str]]:
    selected_images = rng.sample(list(image_assets), int(image_count or 0)) if image_count else []
    selected_videos = rng.sample(list(video_assets), int(video_count or 0)) if video_count else []

    selected_assets = selected_images + selected_videos
    rng.shuffle(selected_assets)

    return selected_assets, selected_images, selected_videos


def select_batch_assets_with_overlap_limit(
    image_assets: Sequence[str],
    video_assets: Sequence[str],
    image_count: int,
    video_count: int,
    rng: random.Random,
    previous_asset_sets: Sequence[Sequence[str]],
    max_overlap: float,
    max_attempts: int = 30,
) -> tuple[list[str], list[str], list[str], float, bool]:
    max_overlap = max(0.0, min(float(max_overlap or 0.0), 1.0))
    max_attempts = max(1, int(max_attempts or 1))

    best_selected_assets: list[str] | None = None
    best_selected_images: list[str] | None = None
    best_selected_videos: list[str] | None = None
    best_overlap = 1.0

    for _ in range(max_attempts):
        selected_assets, selected_images, selected_videos = select_batch_assets(
            image_assets=image_assets,
            video_assets=video_assets,
            image_count=image_count,
            video_count=video_count,
            rng=rng,
        )

        overlap = max_material_overlap(selected_assets, previous_asset_sets)

        if overlap < best_overlap:
            best_selected_assets = selected_assets
            best_selected_images = selected_images
            best_selected_videos = selected_videos
            best_overlap = overlap

        if overlap <= max_overlap:
            return selected_assets, selected_images, selected_videos, overlap, True

    return (
        best_selected_assets or [],
        best_selected_images or [],
        best_selected_videos or [],
        best_overlap,
        False,
    )


def safe_filename(value: str | None, max_length: int = 80) -> str:
    name = str(value or "").strip()
    name = _ILLEGAL_WIN_CHARS_RE.sub("_", name)
    name = re.sub(r"\s+", "_", name)
    name = name.strip(" ._")

    if not name:
        name = "asset_video"

    name = name[:max_length].strip(" ._")
    return name or "asset_video"


def _ensure_unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    for index in range(1, 1000):
        candidate = path.with_name(f"{path.stem}_{index:03d}{path.suffix}")
        if not candidate.exists():
            return candidate

    raise ValueError(f"Cannot create a unique output path near: {path}")


def create_batch_export_dir(export_root: str, batch_id: str) -> Path:
    export_root = str(export_root or "").strip()
    if not export_root:
        raise ValueError("Batch export folder is required.")

    root = Path(export_root).expanduser()
    root.mkdir(parents=True, exist_ok=True)

    probe = root / ".write_test"
    try:
        probe.write_text("ok", encoding="utf-8")
    finally:
        if probe.exists():
            probe.unlink()

    batch_dir = _ensure_unique_path(root / batch_id)
    batch_dir.mkdir(parents=True, exist_ok=False)
    return batch_dir


def copy_video_to_batch_dir(
    source_video_path: str,
    batch_dir: str | Path,
    video_title: str | None,
    index: int,
) -> str:
    source = Path(source_video_path)
    if not source.exists():
        raise FileNotFoundError(f"Generated video not found: {source}")

    batch_dir = Path(batch_dir)
    filename = f"{safe_filename(video_title)}_{int(index):02d}.mp4"
    target = _ensure_unique_path(batch_dir / filename)

    shutil.copy2(source, target)
    return str(target.resolve())


def parse_narrative_angles(raw_text: str | None) -> list[str]:
    text = str(raw_text or "").strip()
    if not text:
        return []

    angles: list[str] = []
    for line in text.splitlines():
        value = line.strip()
        value = re.sub(r"^[\-\*\d\.\)、\)\s]+", "", value).strip()
        if value:
            angles.append(value)

    seen: set[str] = set()
    unique_angles: list[str] = []
    for angle in angles:
        if angle not in seen:
            unique_angles.append(angle)
            seen.add(angle)

    return unique_angles


def choose_narrative_angle(angles: Sequence[str], index: int, rng: random.Random) -> str:
    clean_angles = [str(angle).strip() for angle in angles if str(angle).strip()]
    if not clean_angles:
        return ""

    if int(index) <= len(clean_angles):
        return clean_angles[int(index) - 1]

    return rng.choice(clean_angles)


def normalize_text_for_similarity(text: str | None) -> str:
    value = str(text or "").strip()
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[、。！？!?,，．.「」『』（）()\[\]【】・:：;；\"'`~〜\-ー]", "", value)
    return value.lower()


def text_similarity(left: str | None, right: str | None) -> float:
    left_norm = normalize_text_for_similarity(left)
    right_norm = normalize_text_for_similarity(right)

    if not left_norm or not right_norm:
        return 0.0

    return SequenceMatcher(None, left_norm, right_norm).ratio()


def extract_opening_text(text: str | None, chars: int = 28) -> str:
    clean = normalize_text_for_similarity(text)
    return clean[: max(1, int(chars or 28))]


def max_text_similarity(text: str | None, previous_texts: Sequence[str]) -> float:
    if not previous_texts:
        return 0.0

    return max(text_similarity(text, previous) for previous in previous_texts)


def evaluate_narration_difference(
    narration: str | None,
    previous_narrations: Sequence[str],
    max_narration_similarity: float,
    max_opening_similarity: float,
) -> dict[str, Any]:
    narration_similarity = max_text_similarity(narration, previous_narrations)

    opening = extract_opening_text(narration)
    previous_openings = [extract_opening_text(text) for text in previous_narrations]
    opening_similarity = max_text_similarity(opening, previous_openings)

    warnings: list[str] = []

    if narration_similarity > float(max_narration_similarity):
        warnings.append(
            f"full_narration similarity too high: {narration_similarity:.3f} > {float(max_narration_similarity):.3f}"
        )

    if opening_similarity > float(max_opening_similarity):
        warnings.append(
            f"opening similarity too high: {opening_similarity:.3f} > {float(max_opening_similarity):.3f}"
        )

    return {
        "narration_similarity_max": round(float(narration_similarity), 4),
        "opening_similarity_max": round(float(opening_similarity), 4),
        "opening_text": opening,
        "warnings": warnings,
        "passed": not warnings,
    }


def build_batch_item_intent(
    base_intent: str | None,
    title: str | None,
    index: int,
    total: int,
    selected_assets: Sequence[str],
    variant_seed: str,
    narrative_angle: str | None = None,
    previous_narrations: Sequence[str] | None = None,
    previous_openings: Sequence[str] | None = None,
) -> str:
    base_text = str(base_intent or "").strip()
    if not base_text:
        base_text = str(title or "").strip()

    asset_lines = "\n".join(f"- {Path(path).name}" for path in selected_assets) or "- No selected assets"
    previous_opening_lines = "\n".join(
        f"- {opening}" for opening in (previous_openings or [])[-8:] if str(opening).strip()
    )
    previous_narration_lines = "\n".join(
        f"- {str(text).strip()[:120]}" for text in (previous_narrations or [])[-5:] if str(text).strip()
    )

    if not previous_opening_lines:
        previous_opening_lines = "- None"

    if not previous_narration_lines:
        previous_narration_lines = "- None"

    angle_text = str(narrative_angle or "").strip() or "Create a clearly different emotional angle from the same title and intent."

    variation = f"""
Batch generation constraints:
- This is item {index} of {total}.
- Keep the same core theme from the title and intent.
- This item's narrative angle is: {angle_text}
- Generate a fresh full_narration for this item.
- The final full_narration is the only source for subtitles and voiceover.
- Use natural Japanese only for final subtitles and voiceover.
- Do not use Chinese in subtitles, narration, scene text, or voiceover.
- Make the opening, middle, ending, sentence rhythm, and emotional details clearly different from other batch items.
- Do not reuse the same first sentence, same emotional setup, same ending, or same sentence order.
- Avoid cheap advertising style, exaggerated sadness, dependency, vulgarity, or repeated template wording.
- Prefer concrete daily-life details that match the selected media.
- Variant seed: {variant_seed}

Selected media for this item:
{asset_lines}

Openings already used in this batch. Do not imitate them:
{previous_opening_lines}

Recent narrations already used in this batch. Do not imitate their structure:
{previous_narration_lines}
""".strip()

    if base_text:
        return f"{base_text}\n\n{variation}"

    return variation


def asset_names(asset_paths: Sequence[str]) -> list[str]:
    return [Path(path).name for path in asset_paths or []]


def write_batch_manifest(batch_dir: str | Path, manifest: dict[str, Any]) -> str:
    batch_dir = Path(batch_dir)
    manifest_path = batch_dir / "manifest.json"
    manifest["manifest_path"] = str(manifest_path.resolve())
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(manifest_path.resolve())
