from pathlib import Path
import re

path = Path(r"pixelle_video/pipelines/asset_based.py")
text = path.read_text(encoding="utf-8-sig")

# 1. Replace post_production subtitle stage: SRT -> ASS
old = '''            srt_path = Path(context.task_dir) / "full_narration_subtitles.srt"
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
'''

new = '''            ass_path = Path(context.task_dir) / "full_narration_subtitles.ass"
            self._write_ass_from_full_narration(
                text=getattr(context, "full_narration", ""),
                total_duration=final_duration,
                video_path=str(narrated_video_path),
                output_path=str(ass_path),
            )

            self._burn_subtitles(
                video_path=str(narrated_video_path),
                subtitle_path=str(ass_path),
                output_path=str(final_video_path),
            )
'''

if old in text:
    text = text.replace(old, new, 1)
    print("patched post_production SRT -> ASS")
else:
    print("post_production SRT block not found; maybe already patched")

# 2. Insert ASS helpers before old SRT writer
ass_helpers = r'''
    def _probe_video_size(self, video_path: str) -> tuple[int, int]:
        """Return video width and height. Fallback to 1080x1920."""
        import subprocess

        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=s=x:p=0",
            video_path,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            raw = result.stdout.strip()
            width, height = raw.split("x")
            return int(width), int(height)
        except Exception as exc:
            logger.warning(f"Failed to probe video size, fallback to 1080x1920: {exc}")
            return 1080, 1920

    def _format_ass_time(self, seconds: float) -> str:
        """ASS time format: H:MM:SS.CS"""
        seconds = max(float(seconds or 0), 0.0)
        total_cs = int(round(seconds * 100))
        cs = total_cs % 100
        total_seconds = total_cs // 100
        s = total_seconds % 60
        m = (total_seconds // 60) % 60
        h = total_seconds // 3600
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    def _escape_ass_text(self, text: str) -> str:
        """Escape subtitle text for ASS dialogue line."""
        lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
        safe = r"\N".join(lines)
        safe = safe.replace("{", "").replace("}", "")
        return safe

    def _write_ass_from_full_narration(
        self,
        text: str,
        total_duration: float,
        video_path: str,
        output_path: str,
    ) -> str:
        """
        Generate ASS subtitles from full_narration.

        This is more reliable than SRT + force_style on Windows/FFmpeg.
        Position: lower area, not glued to bottom.
        Font size: dynamic and small enough to stay inside screen.
        """
        width, height = self._probe_video_size(video_path)

        # For 1080x1920, font_size ~= 30.
        # This is clearly visible but much smaller than the original big HTML subtitles.
        font_size = max(24, min(34, int(width / 36)))

        # Lower area but not at the very bottom.
        # For 1920 height, MarginV ~= 345, placing subtitles around lower 75%-82%.
        margin_v = max(260, min(420, int(height * 0.18)))
        margin_lr = max(70, int(width * 0.08))

        chunks = self._split_full_narration_for_subtitles(text, max_chars=22)
        total_duration = max(float(total_duration or 1.0), 1.0)

        if not chunks:
            raise RuntimeError("No subtitle chunks generated from full_narration; refusing to output video without subtitles.")

        weights = [max(len(chunk.replace("\\n", "")), 1) for chunk in chunks]
        total_weight = sum(weights)

        current = 0.0
        events = []

        for index, (chunk, weight) in enumerate(zip(chunks, weights), start=1):
            if index == len(chunks):
                end = total_duration
            else:
                duration = max(0.95, total_duration * weight / total_weight)
                end = min(total_duration, current + duration)

            if end <= current:
                end = min(total_duration, current + 0.95)

            events.append(
                "Dialogue: 0,"
                f"{self._format_ass_time(current)},"
                f"{self._format_ass_time(end)},"
                "Default,,0,0,0,,"
                f"{self._escape_ass_text(chunk)}"
            )

            current = end

        ass = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
WrapStyle: 0
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Meiryo,{font_size},&H00FFFFFF,&H00FFFFFF,&HAA000000,&H66000000,0,0,0,0,100,100,0,0,1,2.0,0.5,2,{margin_lr},{margin_lr},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
{chr(10).join(events)}
"""

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(ass, encoding="utf-8-sig")

        logger.info(
            f"Wrote ASS subtitles: {output_path}, chunks={len(chunks)}, "
            f"font_size={font_size}, margin_v={margin_v}, margin_lr={margin_lr}, size={width}x{height}"
        )

        return output_path

'''

if "    def _write_ass_from_full_narration(" not in text:
    marker = "    def _write_srt_from_full_narration("
    if marker not in text:
        raise SystemExit("Cannot find _write_srt_from_full_narration insertion point.")
    text = text.replace(marker, ass_helpers + "\n" + marker, 1)
    print("inserted ASS subtitle helpers")
else:
    print("ASS subtitle helpers already exist")

# 3. Replace _burn_subtitles completely.
burn_pattern = r'''    def _burn_subtitles\(self, video_path: str, .*?output_path: str\) -> str:
        .*?
            return output_path
'''

burn_replacement = r'''    def _burn_subtitles(self, video_path: str, subtitle_path: str = None, output_path: str = None, srt_path: str = None) -> str:
        """
        Burn ASS subtitles into the final video.

        Important:
        - Do NOT silently copy video if subtitle burn fails.
        - If subtitles fail, raise the FFmpeg error so we can fix it.
        """
        import subprocess

        subtitle_path = subtitle_path or srt_path
        if not subtitle_path:
            raise RuntimeError("subtitle_path is required for subtitle burning.")

        if not Path(subtitle_path).exists():
            raise RuntimeError(f"Subtitle file does not exist: {subtitle_path}")

        if Path(subtitle_path).stat().st_size <= 0:
            raise RuntimeError(f"Subtitle file is empty: {subtitle_path}")

        subtitle_file = Path(subtitle_path).resolve().as_posix()
        subtitle_file = subtitle_file.replace(":", r"\:")
        subtitle_file = subtitle_file.replace("'", r"\\'")

        vf = f"ass='{subtitle_file}'"

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            video_path,
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            "-y",
            output_path,
        ]

        logger.info(f"Burning subtitles with ASS filter: {subtitle_path}")

        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            if not Path(output_path).exists() or Path(output_path).stat().st_size <= 0:
                raise RuntimeError(f"Subtitle burn produced empty output: {output_path}")
            logger.info(f"Burned subtitles into final video: {output_path}")
            return output_path
        except subprocess.CalledProcessError as exc:
            error = exc.stderr or str(exc)
            logger.error(f"Subtitle burn failed: {error}")
            raise RuntimeError(f"Subtitle burn failed. FFmpeg error: {error}")
'''

text, count = re.subn(burn_pattern, lambda m: burn_replacement, text, flags=re.S)

if count != 1:
    print(f"burn_subtitles replace count={count}; expected 1")
else:
    print("replaced _burn_subtitles with strict ASS burner")

path.write_text(text, encoding="utf-8")
print("ASS subtitle repair applied.")
