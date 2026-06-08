from pathlib import Path
import re

ROOT = Path(".")
asset_path = ROOT / "pixelle_video" / "pipelines" / "asset_based.py"
prompt_path = ROOT / "pixelle_video" / "prompts" / "asset_script_generation.py"
frame_processor_path = ROOT / "pixelle_video" / "services" / "frame_processor.py"

# ---------------------------------------------------------------------
# 1. Rewrite prompt: full_narration is the source of voice + subtitles.
# ---------------------------------------------------------------------
prompt_source = r'''# Copyright (C) 2025 AIDC-AI
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
'''

prompt_path.write_text(prompt_source, encoding="utf-8")


# ---------------------------------------------------------------------
# 2. Patch asset_based.py.
# ---------------------------------------------------------------------
text = asset_path.read_text(encoding="utf-8-sig")

# Add imports if needed.
if "import subprocess\n" not in text:
    text = text.replace("import random\n", "import random\nimport subprocess\n")
if "import json\n" not in text:
    text = text.replace("import subprocess\n", "import subprocess\nimport json\n")

# Calculate visual_duration and target_narration_chars after scene_plan.
old = '''        scene_plan = self._build_asset_scene_plan(target_duration=float(duration))
        context.scene_plan = scene_plan
'''
new = '''        scene_plan = self._build_asset_scene_plan(target_duration=float(duration))
        context.scene_plan = scene_plan

        visual_duration = sum(float(item.get("duration") or 0) for item in scene_plan)
        target_narration_chars = self._estimate_narration_chars(
            visual_duration=visual_duration,
            tts_speed=context.params.get("tts_speed", 1.0),
        )
        context.planned_total_duration = visual_duration
        context.target_narration_chars = target_narration_chars
'''
if old in text:
    text = text.replace(old, new, 1)

# Patch prompt call.
old = '''        prompt = build_asset_script_prompt(
            intent=intent,
            duration=duration,
            assets_text=assets_text,
            title=title
        )
'''
new = '''        prompt = build_asset_script_prompt(
            intent=intent,
            duration=duration,
            assets_text=assets_text,
            title=title,
            visual_duration=visual_duration,
            target_narration_chars=target_narration_chars,
        )
'''
if old in text:
    text = text.replace(old, new, 1)

# Patch full narration preparation.
old = '''        planned_total_duration = sum(float(item.get("duration") or 0) for item in scene_plan)
        context.planned_total_duration = planned_total_duration

        fallback_narration = " ".join(
            " ".join(scene.get("narrations") or [])
            for scene in context.script
        )
        context.full_narration = self._build_partner_full_narration(
            intent=intent,
            llm_text=getattr(script, "full_narration", None) or fallback_narration,
            target_duration=planned_total_duration,
        )
        logger.info(
            f"Full narration prepared: chars={len(context.full_narration)}, "
            f"planned_duration={planned_total_duration:.2f}s"
        )
'''
new = '''        fallback_narration = " ".join(
            " ".join(scene.get("narrations") or [])
            for scene in context.script
        )
        context.full_narration = await self._prepare_full_narration(
            context=context,
            llm_text=getattr(script, "full_narration", None) or fallback_narration,
            target_chars=target_narration_chars,
            visual_duration=visual_duration,
        )
        logger.info(
            f"Full narration prepared: chars={len(context.full_narration)}, "
            f"visual_duration={visual_duration:.2f}s, "
            f"target_chars={target_narration_chars}"
        )
'''
if old in text:
    text = text.replace(old, new, 1)

# Disable scene-level subtitles.
old = '''            # Use first narration as the main text (for subtitle)
            # We'll combine all narrations in the audio
            main_narration = " ".join(narrations)  # Combine for subtitle display
'''
new = '''            # Scene-level narration is intentionally empty.
            # Final subtitles are generated from context.full_narration, not from visual scenes.
            main_narration = ""
'''
if old in text:
    text = text.replace(old, new, 1)

# Replace full narration TTS block: no audio time-stretching.
old = '''        fitted_full_audio_path = Path(context.task_dir) / "frames" / "00_full_narration_fitted.mp3"
        context.full_narration_audio_path = self._fit_audio_to_duration(
            str(full_audio_path),
            planned_total_duration,
            str(fitted_full_audio_path),
        )
        logger.info(f"✅ Full narration audio ready: {context.full_narration_audio_path}")
'''
new = '''        context.full_narration_audio_path = await self._generate_full_narration_audio_with_retries(
            context=context,
            config=config,
            target_duration=planned_total_duration,
        )
        context.full_narration_audio_duration = self._probe_video_duration(context.full_narration_audio_path)
        logger.info(
            f"Full narration audio ready: {context.full_narration_audio_path} "
            f"({context.full_narration_audio_duration:.2f}s)"
        )
'''
if old in text:
    text = text.replace(old, new, 1)

# Replace the full narration preparation block if previous patch did not match because of emoji corruption.
pattern = r'''        fitted_full_audio_path = Path\(context\.task_dir\) / "frames" / "00_full_narration_fitted\.mp3"
        context\.full_narration_audio_path = self\._fit_audio_to_duration\(
            str\(full_audio_path\),
            planned_total_duration,
            str\(fitted_full_audio_path\),
        \)
        logger\.info\(f".*?Full narration audio ready: \{context\.full_narration_audio_path\}"\)
'''
text = re.sub(
    pattern,
    '''        context.full_narration_audio_path = await self._generate_full_narration_audio_with_retries(
            context=context,
            config=config,
            target_duration=planned_total_duration,
        )
        context.full_narration_audio_duration = self._probe_video_duration(context.full_narration_audio_path)
        logger.info(
            f"Full narration audio ready: {context.full_narration_audio_path} "
            f"({context.full_narration_audio_duration:.2f}s)"
        )
''',
    text,
    flags=re.S,
)

# Replace post_production audio fitting and add SRT burning.
pattern = r'''        full_narration_audio = getattr\(context, "full_narration_audio_path", None\)
        if full_narration_audio:
            visual_duration = self\._probe_video_duration\(str\(visual_video_path\)\)
            fitted_audio_path = Path\(context\.task_dir\) / "frames" / "00_full_narration_final_fit\.mp3"
            final_audio = self\._fit_audio_to_duration\(
                full_narration_audio,
                visual_duration,
                str\(fitted_audio_path\),
            \)

            self\.core\.video\.merge_audio_video\(
                video=str\(visual_video_path\),
                audio=final_audio,
                output=str\(final_video_path\),
                replace_audio=False,
                audio_volume=1\.0,
                video_volume=1\.0,
                auto_adjust_duration=False,
            \)
        else:
            final_video_path = visual_video_path
'''
replacement = '''        full_narration_audio = getattr(context, "full_narration_audio_path", None)
        if full_narration_audio:
            narrated_video_path = Path(context.task_dir) / f"narrated_{filename}"

            self.core.video.merge_audio_video(
                video=str(visual_video_path),
                audio=full_narration_audio,
                output=str(narrated_video_path),
                replace_audio=False,
                audio_volume=1.0,
                video_volume=1.0,
                auto_adjust_duration=True,
            )

            final_duration = self._probe_video_duration(str(narrated_video_path))
            srt_path = Path(context.task_dir) / "full_narration_subtitles.srt"
            self._write_srt_from_full_narration(
                text=getattr(context, "full_narration", ""),
                total_duration=final_duration,
                output_path=str(srt_path),
            )

            self._burn_subtitles(
                video_path=str(narrated_video_path),
                srt_path=str(srt_path),
                output_path=str(final_video_path),
            )
        else:
            final_video_path = visual_video_path
'''
text, post_count = re.subn(pattern, replacement, text, flags=re.S)

# Replace garbled helper block from _build_partner_full_narration through before _normalize_script_to_scene_plan.
helper_pattern = r'''    def _build_partner_full_narration\(self, intent: str, llm_text: str, target_duration: float\) -> str:
        .*?
    def _normalize_script_to_scene_plan\(
'''
helper_replacement = r'''    def _core_theme_keywords(self) -> tuple[str, ...]:
        return (
            "\u0033\u0038\u6b73",
            "\u0034\u0030\u6b73\u4ee5\u4e0a",
            "\u771f\u5263",
            "\u51fa\u4f1a",
            "\u7a4f\u3084\u304b",
        )

    def _estimate_narration_chars(self, visual_duration: float, tts_speed: float = 1.0) -> int:
        """
        Estimate Japanese narration length from target visual duration and fixed TTS speed.
        Do not time-stretch audio. Generate the correct text length instead.
        """
        visual_duration = max(float(visual_duration or 1.0), 1.0)
        try:
            speed = float(tts_speed or 1.0)
        except Exception:
            speed = 1.0

        # Gentle Japanese female narration: about 5.2 chars/sec at 1.0x.
        chars = int(visual_duration * 5.2 * speed)
        return max(18, min(chars, 260))

    def _clean_full_narration_text(self, text: str) -> str:
        import re

        text = str(text or "").strip()
        text = text.replace("```json", "").replace("```", "").strip()

        # If a model returns JSON as text, try extracting full_narration.
        if text.startswith("{") and text.endswith("}"):
            try:
                data = json.loads(text)
                if isinstance(data, dict) and data.get("full_narration"):
                    text = str(data["full_narration"])
            except Exception:
                pass

        text = re.sub(r"\s+", "", text)
        return text.strip()

    def _has_required_partner_theme(self, text: str) -> bool:
        text = self._clean_full_narration_text(text)
        if not text:
            return False

        has_age = "\u0033\u0038" in text and "\u0034\u0030" in text
        has_meeting = any(token in text for token in ("\u51fa\u4f1a", "\u30d1\u30fc\u30c8\u30ca\u30fc", "\u4f34\u4fb6"))
        has_serious = "\u771f\u5263" in text
        has_calm = any(token in text for token in ("\u7a4f\u3084\u304b", "\u843d\u3061\u7740\u3044\u305f", "\u8aa0\u5b9f"))

        return has_age and has_meeting and has_serious and has_calm

    async def _prepare_full_narration(
        self,
        context: PipelineContext,
        llm_text: str,
        target_chars: int,
        visual_duration: float,
    ) -> str:
        """
        Prepare a continuous narration that is used for both voiceover and subtitles.
        Text length is controlled here. Audio speed is not modified.
        """
        text = self._clean_full_narration_text(llm_text)
        min_chars = max(12, int(target_chars * 0.82))
        max_chars = max(min_chars + 4, int(target_chars * 1.18))

        if self._has_required_partner_theme(text) and min_chars <= len(text) <= max_chars:
            return text

        return await self._rewrite_full_narration_to_length(
            context=context,
            current_text=text,
            target_chars=target_chars,
            visual_duration=visual_duration,
            reason="initial_validation",
        )

    async def _rewrite_full_narration_to_length(
        self,
        context: PipelineContext,
        current_text: str,
        target_chars: int,
        visual_duration: float,
        reason: str,
    ) -> str:
        """
        Ask the configured LLM to rewrite the full narration to the required length.
        Source code uses unicode escapes to avoid PowerShell encoding damage.
        """
        target_chars = max(18, int(target_chars))
        min_chars = max(12, int(target_chars * 0.88))
        max_chars = max(min_chars + 4, int(target_chars * 1.12))

        keywords = ", ".join(self._core_theme_keywords())
        intent = context.request.get("intent") or context.input_text or ""

        prompt = f"""
You are rewriting a Japanese voiceover for a short video.

The voiceover must be ONE continuous natural Japanese monologue.
It will be used as BOTH the final voiceover and the final subtitles.

Target visual duration: {visual_duration:.2f} seconds.
Target length: about {target_chars} Japanese characters.
Acceptable length range: {min_chars}-{max_chars} Japanese characters.

Required ideas / keywords:
{keywords}

Tone:
- gentle
- natural
- sincere
- warm
- slightly lonely but positive
- first person, as a 38-year-old single woman
- addressed toward calm sincere men over 40

Avoid:
- Chinese
- English
- cheap advertisement style
- exaggerated sadness
- vulgar language
- dependency or begging

User intent:
{intent}

Current narration:
{current_text}

Reason for rewrite:
{reason}

Return ONLY the rewritten Japanese narration. No markdown. No explanation.
""".strip()

        try:
            result = await self.core.llm(
                prompt=prompt,
                temperature=0.4,
                max_tokens=700,
            )
            rewritten = self._clean_full_narration_text(str(result))

            if rewritten:
                logger.info(
                    f"Rewritten full narration: chars={len(rewritten)}, "
                    f"target={target_chars}, reason={reason}"
                )
                return rewritten
        except Exception as exc:
            logger.warning(f"Failed to rewrite full narration with LLM: {exc}")

        return self._clean_full_narration_text(current_text)

    async def _generate_full_narration_audio_with_retries(
        self,
        context: PipelineContext,
        config,
        target_duration: float,
    ) -> str:
        """
        Generate TTS without time-stretching.
        If the natural TTS duration is too long/short, rewrite text and re-generate audio.
        """
        target_duration = max(float(target_duration or 1.0), 1.0)
        lower = target_duration * 0.88
        upper = target_duration * 1.12

        frames_dir = Path(context.task_dir) / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        current_text = self._clean_full_narration_text(getattr(context, "full_narration", ""))
        last_audio_path = None
        last_duration = 0.0

        for attempt in range(3):
            audio_path = frames_dir / f"00_full_narration_attempt_{attempt + 1}.mp3"

            await self.core.tts(
                text=current_text,
                output_path=str(audio_path),
                voice=config.voice_id,
                speed=config.tts_speed,
            )

            actual_duration = self._probe_video_duration(str(audio_path))
            last_audio_path = str(audio_path)
            last_duration = actual_duration

            logger.info(
                f"Full narration TTS attempt {attempt + 1}: "
                f"actual={actual_duration:.2f}s, target={target_duration:.2f}s, "
                f"chars={len(current_text)}"
            )

            if lower <= actual_duration <= upper:
                context.full_narration = current_text
                return str(audio_path)

            if attempt >= 2 or actual_duration <= 0:
                break

            adjusted_chars = int(len(current_text) * target_duration / actual_duration)
            adjusted_chars = max(18, min(adjusted_chars, 260))

            reason = (
                "audio_too_long_make_text_shorter"
                if actual_duration > upper
                else "audio_too_short_make_text_longer"
            )

            current_text = await self._rewrite_full_narration_to_length(
                context=context,
                current_text=current_text,
                target_chars=adjusted_chars,
                visual_duration=target_duration,
                reason=reason,
            )

        context.full_narration = current_text
        logger.warning(
            f"Using closest full narration audio after retries: "
            f"duration={last_duration:.2f}s, target={target_duration:.2f}s"
        )
        return last_audio_path

    def _normalize_script_to_scene_plan(
'''
text, helper_count = re.subn(helper_pattern, lambda m: helper_replacement, text, flags=re.S)

# If helper replacement did not match, insert helpers before _normalize_script_to_scene_plan.
if helper_count == 0 and "    def _estimate_narration_chars(" not in text:
    insert_at = text.find("    def _normalize_script_to_scene_plan(")
    if insert_at == -1:
        raise SystemExit("Could not find _normalize_script_to_scene_plan insertion point.")
    text = text[:insert_at] + helper_replacement.replace("    def _normalize_script_to_scene_plan(\n", "") + text[insert_at:]

# Replace normalize function body to avoid scene narration as subtitle.
normalize_pattern = r'''    def _normalize_script_to_scene_plan\(
        self,
        raw_script: List\[Dict\[str, Any\]\],
        scene_plan: List\[Dict\[str, Any\]\],
    \) -> List\[Dict\[str, Any\]\]:
        .*?
        return normalized
'''
normalize_replacement = r'''    def _normalize_script_to_scene_plan(
        self,
        raw_script: List[Dict[str, Any]],
        scene_plan: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Force LLM output to match the planned visual schedule.
        Scene narrations are intentionally empty because final subtitles come from full_narration.
        """
        normalized: List[Dict[str, Any]] = []

        for planned in scene_plan:
            normalized.append({
                "scene_number": planned["scene_number"],
                "asset_path": planned["asset_path"],
                "narrations": [""],
                "duration": planned["duration"],
            })

        return normalized
'''
text, _ = re.subn(normalize_pattern, normalize_replacement, text, flags=re.S)

# Disable _fit_audio_to_duration if it still exists.
fit_pattern = r'''    def _fit_audio_to_duration\(self, input_audio: str, target_duration: float, output_audio: str\) -> str:
        .*?
            return input_audio
'''
fit_replacement = r'''    def _fit_audio_to_duration(self, input_audio: str, target_duration: float, output_audio: str) -> str:
        """
        Deprecated. Do not time-stretch narration audio.
        Duration must be controlled by narration text length, not by audio filters.
        """
        return input_audio
'''
text, _ = re.subn(fit_pattern, fit_replacement, text, flags=re.S)

# Add subtitle helpers before Helper methods.
subtitle_helpers = r'''
    def _split_full_narration_for_subtitles(self, text: str, max_chars: int = 18) -> List[str]:
        """Split full narration into readable subtitle chunks."""
        import re

        text = self._clean_full_narration_text(text)
        if not text:
            return []

        rough_parts = re.split(r"(?<=[\u3002\uff01\uff1f!?])", text)
        chunks: List[str] = []

        for part in rough_parts:
            part = part.strip()
            if not part:
                continue

            while len(part) > max_chars:
                cut = max_chars
                for sep in ("\u3001", "\uff0c", ","):
                    pos = part.rfind(sep, 0, max_chars + 1)
                    if pos >= 6:
                        cut = pos + 1
                        break

                chunks.append(part[:cut].strip())
                part = part[cut:].strip()

            if part:
                chunks.append(part)

        return [chunk for chunk in chunks if chunk]

    def _format_srt_time(self, seconds: float) -> str:
        seconds = max(float(seconds or 0), 0.0)
        millis = int(round((seconds - int(seconds)) * 1000))
        total_seconds = int(seconds)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def _write_srt_from_full_narration(self, text: str, total_duration: float, output_path: str) -> str:
        """
        Generate SRT subtitles from full_narration.
        Subtitle timing follows the full narration duration, not visual scenes.
        """
        chunks = self._split_full_narration_for_subtitles(text)
        total_duration = max(float(total_duration or 1.0), 1.0)

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        if not chunks:
            Path(output_path).write_text("", encoding="utf-8")
            return output_path

        weights = [max(len(chunk), 1) for chunk in chunks]
        total_weight = sum(weights)

        current = 0.0
        lines = []

        for index, (chunk, weight) in enumerate(zip(chunks, weights), start=1):
            duration = total_duration * weight / total_weight
            duration = max(1.0, min(duration, 3.8))

            # Keep final subtitle ending aligned with total duration.
            if index == len(chunks):
                end = total_duration
            else:
                end = min(total_duration, current + duration)

            if end <= current:
                end = min(total_duration, current + 1.0)

            lines.append(str(index))
            lines.append(f"{self._format_srt_time(current)} --> {self._format_srt_time(end)}")
            lines.append(chunk)
            lines.append("")

            current = end

        Path(output_path).write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"Wrote full narration subtitles: {output_path}, chunks={len(chunks)}")
        return output_path

    def _escape_subtitle_filter_path(self, path: str) -> str:
        value = Path(path).resolve().as_posix()
        value = value.replace(":", r"\:")
        value = value.replace("'", r"\\'")
        return value

    def _burn_subtitles(self, video_path: str, srt_path: str, output_path: str) -> str:
        """Burn SRT subtitles into the final video."""
        subtitle_path = self._escape_subtitle_filter_path(srt_path)
        force_style = (
            "FontName=Meiryo,"
            "Fontsize=18,"
            "PrimaryColour=&HFFFFFF&,"
            "OutlineColour=&H80000000&,"
            "BorderStyle=1,"
            "Outline=2,"
            "Shadow=1,"
            "Alignment=2,"
            "MarginV=120"
        )

        vf = f"subtitles='{subtitle_path}':force_style='{force_style}'"

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            video_path,
            "-vf",
            vf,
            "-c:a",
            "copy",
            "-y",
            output_path,
        ]

        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            logger.info(f"Burned subtitles into final video: {output_path}")
            return output_path
        except subprocess.CalledProcessError as exc:
            logger.warning(f"Subtitle burn failed, fallback to no subtitles: {exc.stderr}")
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy(video_path, output_path)
            return output_path

'''

if "    def _write_srt_from_full_narration(" not in text:
    marker = "    # Helper methods"
    if marker not in text:
        raise SystemExit("Could not find helper marker for subtitle helpers.")
    text = text.replace(marker, subtitle_helpers + "\n" + marker, 1)

asset_path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------
# 3. Patch frame_processor.py.
#    - video assets keep full duration when using silent placeholder audio
#    - image scenes use planned target duration
# ---------------------------------------------------------------------
fp = frame_processor_path.read_text(encoding="utf-8-sig")

fp = fp.replace(
'''                replace_audio=True,  # Replace video audio with narration
                audio_volume=1.0
''',
'''                replace_audio=True,  # Replace video audio with narration
                audio_volume=1.0,
                auto_adjust_duration=False
''',
1
)

fp = fp.replace(
'''                output=output_path,
                fps=config.video_fps
''',
'''                output=output_path,
                fps=config.video_fps,
                duration=getattr(frame, "target_duration", None) or frame.duration or None
''',
1
)

frame_processor_path.write_text(fp, encoding="utf-8")

print("All asset-based full narration / subtitle fixes applied.")
