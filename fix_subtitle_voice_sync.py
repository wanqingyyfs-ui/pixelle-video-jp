from pathlib import Path
import re

path = Path(r"pixelle_video/pipelines/asset_based.py")
text = path.read_text(encoding="utf-8-sig")

# ------------------------------------------------------------
# 1. Replace full narration TTS generator:
#    use edge-tts CLI to generate audio + VTT subtitles together.
# ------------------------------------------------------------
pattern = r'''    async def _generate_full_narration_audio_with_retries\(
        self,
        context: PipelineContext,
        config,
        target_duration: float,
    \) -> str:
        .*?
        return last_audio_path
'''

replacement = r'''    async def _generate_full_narration_audio_with_retries(
        self,
        context: PipelineContext,
        config,
        target_duration: float,
    ) -> str:
        """
        Generate full narration audio and VTT subtitles together using Edge TTS.

        This keeps subtitle timing aligned with the actual TTS voice rhythm.
        Audio is never time-stretched.
        """
        target_duration = max(float(target_duration or 1.0), 1.0)
        lower = target_duration * 0.88
        upper = target_duration * 1.12

        frames_dir = Path(context.task_dir) / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)

        current_text = self._clean_full_narration_text(getattr(context, "full_narration", ""))
        last_audio_path = None
        last_vtt_path = None
        last_duration = 0.0

        for attempt in range(3):
            audio_path = frames_dir / f"00_full_narration_attempt_{attempt + 1}.mp3"
            vtt_path = frames_dir / f"00_full_narration_attempt_{attempt + 1}.vtt"

            self._generate_edge_tts_audio_and_vtt(
                text=current_text,
                voice=config.voice_id or "ja-JP-NanamiNeural",
                speed=config.tts_speed or 1.0,
                audio_path=str(audio_path),
                vtt_path=str(vtt_path),
            )

            actual_duration = self._probe_video_duration(str(audio_path))
            last_audio_path = str(audio_path)
            last_vtt_path = str(vtt_path)
            last_duration = actual_duration

            logger.info(
                f"Full narration TTS attempt {attempt + 1}: "
                f"actual={actual_duration:.2f}s, target={target_duration:.2f}s, "
                f"chars={len(current_text)}, vtt={vtt_path.exists()}"
            )

            if lower <= actual_duration <= upper:
                context.full_narration = current_text
                context.full_narration_vtt_path = str(vtt_path)
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
        context.full_narration_vtt_path = last_vtt_path

        logger.warning(
            f"Using closest full narration audio after retries: "
            f"duration={last_duration:.2f}s, target={target_duration:.2f}s"
        )
        return last_audio_path

    def _edge_tts_rate(self, speed: float) -> str:
        """Convert speed multiplier to Edge TTS CLI rate."""
        try:
            speed = float(speed or 1.0)
        except Exception:
            speed = 1.0

        percent = int(round((speed - 1.0) * 100))
        sign = "+" if percent >= 0 else ""
        return f"{sign}{percent}%"

    def _generate_edge_tts_audio_and_vtt(
        self,
        text: str,
        voice: str,
        speed: float,
        audio_path: str,
        vtt_path: str,
    ) -> None:
        """
        Generate audio and VTT subtitle timestamps in the same Edge TTS run.
        Only ja-JP-NanamiNeural is trusted in this local environment.
        """
        import subprocess
        import sys

        if str(voice).startswith("ja-JP-") and voice != "ja-JP-NanamiNeural":
            logger.warning(f"Unsupported Japanese voice '{voice}', fallback to ja-JP-NanamiNeural")
            voice = "ja-JP-NanamiNeural"

        Path(audio_path).parent.mkdir(parents=True, exist_ok=True)
        Path(vtt_path).parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            sys.executable,
            "-m",
            "edge_tts",
            "--voice",
            voice,
            "--rate",
            self._edge_tts_rate(speed),
            "--text",
            text,
            "--write-media",
            audio_path,
            "--write-subtitles",
            vtt_path,
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        if result.returncode != 0:
            raise RuntimeError(f"edge_tts failed: {result.stderr or result.stdout}")

        if not Path(audio_path).exists() or Path(audio_path).stat().st_size <= 0:
            raise RuntimeError(f"Edge TTS did not create audio: {audio_path}")

        if not Path(vtt_path).exists() or Path(vtt_path).stat().st_size <= 0:
            raise RuntimeError(f"Edge TTS did not create VTT subtitles: {vtt_path}")
'''

text, count = re.subn(pattern, lambda m: replacement, text, flags=re.S)
print(f"patched TTS audio+VTT generator: {count}")

# ------------------------------------------------------------
# 2. Patch post_production: use VTT timing if available.
# ------------------------------------------------------------
old = '''            ass_path = Path(context.task_dir) / "full_narration_subtitles.ass"
            self._write_ass_from_full_narration(
                text=getattr(context, "full_narration", ""),
                total_duration=final_duration,
                video_path=str(narrated_video_path),
                output_path=str(ass_path),
            )
'''

new = '''            ass_path = Path(context.task_dir) / "full_narration_subtitles.ass"
            vtt_path = getattr(context, "full_narration_vtt_path", None)

            if vtt_path and Path(vtt_path).exists():
                self._write_ass_from_vtt(
                    vtt_path=str(vtt_path),
                    video_path=str(narrated_video_path),
                    output_path=str(ass_path),
                )
            else:
                logger.warning("No VTT subtitle timing found, falling back to text-based subtitle timing.")
                self._write_ass_from_full_narration(
                    text=getattr(context, "full_narration", ""),
                    total_duration=final_duration,
                    video_path=str(narrated_video_path),
                    output_path=str(ass_path),
                )
'''

if old in text:
    text = text.replace(old, new, 1)
    print("patched post_production to use VTT timing")
else:
    print("post_production ASS block not found; maybe already changed")

# ------------------------------------------------------------
# 3. Insert VTT -> ASS helpers before _write_ass_from_full_narration.
# ------------------------------------------------------------
helpers = r'''    def _parse_vtt_time(self, value: str) -> float:
        """Parse VTT timestamp to seconds."""
        value = str(value or "").strip().replace(",", ".")
        parts = value.split(":")

        try:
            if len(parts) == 3:
                hours = int(parts[0])
                minutes = int(parts[1])
                seconds = float(parts[2])
                return hours * 3600 + minutes * 60 + seconds

            if len(parts) == 2:
                minutes = int(parts[0])
                seconds = float(parts[1])
                return minutes * 60 + seconds
        except Exception:
            return 0.0

        return 0.0

    def _clean_vtt_text(self, value: str) -> str:
        """Clean VTT cue text."""
        import re

        value = str(value or "").strip()
        value = re.sub(r"<[^>]+>", "", value)
        value = value.replace("&nbsp;", " ")
        value = value.replace("&amp;", "&")
        value = value.replace("&lt;", "<")
        value = value.replace("&gt;", ">")
        return value.strip()

    def _read_vtt_cues(self, vtt_path: str) -> List[Dict[str, Any]]:
        """Read Edge TTS VTT cues."""
        lines = Path(vtt_path).read_text(encoding="utf-8-sig").splitlines()
        cues: List[Dict[str, Any]] = []

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            if "-->" not in line:
                i += 1
                continue

            start_raw, end_raw = [part.strip() for part in line.split("-->", 1)]
            end_raw = end_raw.split(" ")[0].strip()

            start = self._parse_vtt_time(start_raw)
            end = self._parse_vtt_time(end_raw)

            i += 1
            text_lines = []

            while i < len(lines) and lines[i].strip():
                text_lines.append(lines[i].strip())
                i += 1

            cue_text = self._clean_vtt_text("".join(text_lines))

            if cue_text and end > start:
                cues.append({
                    "start": start,
                    "end": end,
                    "text": cue_text,
                })

            i += 1

        return cues

    def _group_vtt_cues_for_subtitles(
        self,
        cues: List[Dict[str, Any]],
        max_chars: int = 18,
        line_limit: int = 9,
    ) -> List[Dict[str, Any]]:
        """
        Group Edge TTS VTT cues into readable subtitle chunks.

        The start/end time comes from actual TTS cue timestamps.
        This is much closer to the voice rhythm than proportional timing.
        """
        groups: List[Dict[str, Any]] = []
        current_text = ""
        current_start = None
        current_end = None

        sentence_end = ("\u3002", "\uff01", "\uff1f", "!", "?")
        soft_break = ("\u3001", "\uff0c", ",")

        for cue in cues:
            cue_text = str(cue.get("text") or "").strip()
            if not cue_text:
                continue

            if current_start is None:
                current_start = float(cue["start"])

            candidate = current_text + cue_text
            current_end = float(cue["end"])

            should_flush = False

            if len(candidate) >= max_chars:
                should_flush = True
            if cue_text.endswith(sentence_end):
                should_flush = True
            if len(candidate) >= max_chars - 4 and cue_text.endswith(soft_break):
                should_flush = True

            current_text = candidate

            if should_flush:
                groups.append({
                    "start": current_start,
                    "end": current_end,
                    "text": self._wrap_subtitle_chunk(current_text, line_limit=line_limit),
                })
                current_text = ""
                current_start = None
                current_end = None

        if current_text and current_start is not None and current_end is not None:
            groups.append({
                "start": current_start,
                "end": current_end,
                "text": self._wrap_subtitle_chunk(current_text, line_limit=line_limit),
            })

        # Add tiny breathing room but do not overlap next subtitle.
        for index, group in enumerate(groups):
            if index + 1 < len(groups):
                group["end"] = min(group["end"] + 0.08, groups[index + 1]["start"] - 0.02)
            else:
                group["end"] = group["end"] + 0.10

            if group["end"] <= group["start"]:
                group["end"] = group["start"] + 0.35

        return groups

    def _write_ass_from_vtt(self, vtt_path: str, video_path: str, output_path: str) -> str:
        """
        Convert Edge TTS VTT timing to ASS subtitles.

        This makes subtitle switching follow the actual TTS voice timing.
        """
        width, height = self._probe_video_size(video_path)

        # Bigger TikTok vertical subtitle.
        # For 1080px width, font_size ~= 83.
        font_size = max(72, min(96, int(width / 13)))

        # Lower area, not glued to bottom.
        margin_v = max(230, min(360, int(height * 0.14)))
        margin_lr = max(60, int(width * 0.06))

        cues = self._read_vtt_cues(vtt_path)
        groups = self._group_vtt_cues_for_subtitles(
            cues,
            max_chars=18,
            line_limit=9,
        )

        if not groups:
            raise RuntimeError(f"No subtitle groups generated from VTT: {vtt_path}")

        events = []
        for group in groups:
            events.append(
                "Dialogue: 0,"
                f"{self._format_ass_time(group['start'])},"
                f"{self._format_ass_time(group['end'])},"
                "Default,,0,0,0,,"
                f"{self._escape_ass_text(group['text'])}"
            )

        ass = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
WrapStyle: 0
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Meiryo,{font_size},&H00FFFFFF,&H00FFFFFF,&HAA000000,&H66000000,1,0,0,0,100,100,0,0,1,2.6,0.8,2,{margin_lr},{margin_lr},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
{chr(10).join(events)}
"""

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(ass, encoding="utf-8-sig")

        logger.info(
            f"Wrote ASS subtitles from VTT: {output_path}, "
            f"vtt_cues={len(cues)}, groups={len(groups)}, "
            f"font_size={font_size}, margin_v={margin_v}, size={width}x{height}"
        )

        return output_path

'''

if "    def _write_ass_from_vtt(" not in text:
    marker = "    def _write_ass_from_full_narration("
    if marker not in text:
        raise SystemExit("Cannot find _write_ass_from_full_narration insertion point.")
    text = text.replace(marker, helpers + "\n" + marker, 1)
    print("inserted VTT -> ASS subtitle helpers")
else:
    print("VTT -> ASS helpers already exist")

# ------------------------------------------------------------
# 4. Make fallback ASS font also bigger.
# ------------------------------------------------------------
text = re.sub(
    r'font_size = max\([0-9]+, min\([0-9]+, int\(width / [0-9]+\)\)\)',
    'font_size = max(72, min(96, int(width / 13)))',
    text,
    count=1,
)

path.write_text(text, encoding="utf-8")
print("subtitle size and voice-synced timing patch applied.")
