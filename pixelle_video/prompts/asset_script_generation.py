# Copyright (C) 2025 AIDC-AI
#
# Licensed under the Apache License, Version 2.0

"""
Asset-based video script generation prompt

For generating Japanese video scripts based on user-provided assets.
"""

ASSET_SCRIPT_GENERATION_PROMPT = """You are a professional Japanese short-video script creator.

Your task is to generate a {duration}-second Japanese video script based on the user's intent and uploaded assets.

## Absolute Language Rules
- All narration text must be natural Japanese.
- Do not output Chinese narration.
- Do not output English narration unless it is a brand name or unavoidable proper noun.
- The subtitles will be created directly from `narrations`, so every sentence in `narrations` must be suitable as Japanese subtitles.
- Use a gentle, natural, Japanese female-narrator tone.
- Keep sentences short, smooth, emotional, and spoken-language friendly.

## Requirements
{title_section}- Video Intent: {intent}
- Target Duration: {duration} seconds

## Available Assets
Use exact paths from the list below. Do not modify asset paths.

{assets_text}

## Creation Guidelines
1. Generate 3-8 scenes depending on target duration.
2. Assign one asset from available assets to each scene.
3. Each scene should contain 1-2 short Japanese narration sentences.
4. Try to use all available assets when possible.
5. Total duration should approximately equal {duration} seconds.
6. The style should feel warm, elegant, gentle, slightly lonely, but still positive.
7. Avoid exaggerated, cheap advertising language.
8. Avoid Chinese words completely.
{title_instruction}

## Output Requirements
Provide for each scene:
- scene_number: Scene number starting from 1
- asset_path: Exact path selected from the available assets list
- narrations: Array of natural Japanese narration sentences
- duration: Estimated duration in seconds

Now generate the Japanese video script."""

def build_asset_script_prompt(
    intent: str,
    duration: int,
    assets_text: str,
    title: str = ""
) -> str:
    """
    Build asset-based script generation prompt.
    """

    title_section = f"- Video Title: {title}\n" if title else ""
    title_instruction = f"9. Narration content should be consistent with the video title: {title}\n" if title else ""

    return ASSET_SCRIPT_GENERATION_PROMPT.format(
        duration=duration,
        title_section=title_section,
        intent=intent,
        assets_text=assets_text,
        title_instruction=title_instruction
    )
