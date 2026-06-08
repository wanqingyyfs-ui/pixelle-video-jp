# Copyright (C) 2025 AIDC-AI
#
# Licensed under the Apache License, Version 2.0

"""
Asset-based video script generation prompt.

The generated full_narration is the single source for both voiceover and subtitles.
Scene narrations are not used as subtitles.
"""

ASSET_SCRIPT_GENERATION_PROMPT = """You are a professional Japanese short-video script creator.

Your task is to generate a Japanese short-video script based on the user's intent and uploaded assets.

## Absolute Language Rules
- All narration text must be natural Japanese.
- Do not output Chinese narration.
- Do not output English narration unless it is an unavoidable proper noun.
- Use a gentle, natural, Japanese female-narrator tone.
- Keep the narration smooth, warm, sincere, and spoken-language friendly.

## Most Important Rule
- `full_narration` is the ONLY source for the final voiceover and subtitles.
- The subtitles will be generated from `full_narration`.
- Scene `narrations` are NOT used as the final subtitles.
- The visual scenes only control asset order and visual timing.

## Required Theme
The `full_narration` must clearly express these ideas:
- \u0033\u0038\u6b73
- \u72ec\u8eab\u5973\u6027
- \u0034\u0030\u6b73\u4ee5\u4e0a
- \u843d\u3061\u7740\u3044\u305f\u5927\u4eba\u306e\u7537\u6027
- \u771f\u5263\u306a\u51fa\u4f1a\u3044
- \u3053\u308c\u304b\u3089\u306e\u4eba\u751f\u3092\u4e00\u7dd2\u306b\u7a4f\u3084\u304b\u306b\u6b69\u304d\u305f\u3044

## Requirements
{title_section}- Video Intent: {intent}
- User requested duration: {duration} seconds
- Planned visual duration: {visual_duration} seconds
- Target full_narration length: about {target_narration_chars} Japanese characters

## Available Assets
Use exact paths from the list below. Do not modify asset paths.

{assets_text}

## Visual Scene Rules
1. If the asset list contains `Planned Scene`, generate exactly those planned scenes in the same order.
2. Do not add extra scenes.
3. Do not remove planned scenes.
4. Use the exact asset_path from each planned scene.
5. Scene data is only for visual scheduling.
6. Scene `narrations` may be empty or very short, because the final subtitles come from `full_narration`.

## Full Narration Rules
1. Generate ONE continuous `full_narration` for the whole video.
2. It must sound like one complete first-person monologue by a 38-year-old woman.
3. It must not sound like separate captions for each image.
4. It must match approximately {target_narration_chars} Japanese characters.
5. It must be suitable for a natural female Japanese TTS voice at the selected speed.
6. It must not be too dramatic, too sad, too dependent, vulgar, or like a cheap advertisement.
7. It must be warm, gentle, sincere, slightly lonely, but forward-looking.
8. Use soft spoken Japanese, not stiff written Japanese.
9. Use short natural sentences with gentle pauses.
10. Avoid formal announcement style, sales style, and over-polished advertising copy.
11. The narration should sound like a real woman speaking quietly and naturally to one sincere person.
{title_instruction}

## Output Requirements
Return valid JSON only.
Required fields:
- full_narration: one continuous natural Japanese narration
- scenes: list of visual scenes

Each scene must include:
- scene_number
- asset_path
- narrations
- duration

Now generate the JSON."""

def build_asset_script_prompt(
    intent: str,
    duration: int,
    assets_text: str,
    title: str = "",
    visual_duration=None,
    target_narration_chars=None,
) -> str:
    title_section = f"- Video Title: {title}\n" if title else ""
    title_instruction = (
        f"8. The narration content should be consistent with the video title: {title}\n"
        if title else ""
    )

    if visual_duration is None:
        visual_duration = duration
    if target_narration_chars is None:
        target_narration_chars = max(20, int(float(visual_duration) * 5.2))

    return ASSET_SCRIPT_GENERATION_PROMPT.format(
        duration=duration,
        visual_duration=f"{float(visual_duration):.2f}",
        target_narration_chars=int(target_narration_chars),
        title_section=title_section,
        intent=intent,
        assets_text=assets_text,
        title_instruction=title_instruction,
    )
