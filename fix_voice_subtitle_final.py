from pathlib import Path
import re

ROOT = Path(".")
asset_path = ROOT / "pixelle_video" / "pipelines" / "asset_based.py"
tts_service_path = ROOT / "pixelle_video" / "services" / "tts_service.py"
tts_voices_path = ROOT / "pixelle_video" / "tts_voices.py"
prompt_path = ROOT / "pixelle_video" / "prompts" / "asset_script_generation.py"

# ---------------------------------------------------------------------
# 1. Force all Japanese defaults back to stable Nanami.
# ---------------------------------------------------------------------
jp_voices = [
    "ja-JP-ShioriNeural",
    "ja-JP-MayuNeural",
    "ja-JP-AoiNeural",
    "ja-JP-KeitaNeural",
    "ja-JP-DaichiNeural",
    "ja-JP-NaokiNeural",
]

files_to_patch_voice = [
    ROOT / "pixelle_video" / "config" / "schema.py",
    ROOT / "pixelle_video" / "services" / "tts_service.py",
    ROOT / "pixelle_video" / "pipelines" / "asset_based.py",
    ROOT / "pixelle_video" / "pipelines" / "custom.py",
    ROOT / "pixelle_video" / "pipelines" / "standard.py",
    ROOT / "web" / "pipelines" / "asset_based.py",
    ROOT / "web" / "components" / "digital_tts_config.py",
    ROOT / "web" / "components" / "style_config.py",
    ROOT / "web" / "pipelines" / "digital_human.py",
    ROOT / "config.yaml",
]

for path in files_to_patch_voice:
    if not path.exists():
        continue
    text = path.read_text(encoding="utf-8-sig")
    original = text

    for voice in jp_voices:
        text = text.replace(voice, "ja-JP-NanamiNeural")

    text = text.replace(
        'tts_speed=context.params.get("tts_speed", 0.92)',
        'tts_speed=context.params.get("tts_speed", 1.0)'
    )

    text = re.sub(
        r'video_params\.get\("tts_speed",\s*[0-9.]+\)',
        'video_params.get("tts_speed", 1.0)',
        text
    )

    if text != original:
        path.write_text(text, encoding="utf-8")
        print(f"patched voice defaults: {path}")

# ---------------------------------------------------------------------
# 2. Add hard fallback in TTS service:
#    any unsupported Japanese voice -> Nanami.
# ---------------------------------------------------------------------
if tts_service_path.exists():
    text = tts_service_path.read_text(encoding="utf-8-sig")

    text = re.sub(
        r'final_voice = voice or local_config\.get\("voice", "ja-JP-[^"]+Neural"\)',
        'final_voice = voice or local_config.get("voice", "ja-JP-NanamiNeural")',
        text
    )

    if "Only Nanami is verified stable for Japanese TTS" not in text:
        target = '        final_voice = voice or local_config.get("voice", "ja-JP-NanamiNeural")\n'
        insert = '''        final_voice = voice or local_config.get("voice", "ja-JP-NanamiNeural")
        # Only Nanami is verified stable for Japanese TTS in this local environment.
        if str(final_voice).startswith("ja-JP-") and final_voice != "ja-JP-NanamiNeural":
            logger.warning(f"Unsupported Japanese Edge TTS voice '{final_voice}', fallback to ja-JP-NanamiNeural")
            final_voice = "ja-JP-NanamiNeural"
'''
        if target in text:
            text = text.replace(target, insert, 1)

    tts_service_path.write_text(text, encoding="utf-8")
    print("patched TTS fallback to Nanami")

# ---------------------------------------------------------------------
# 3. Hide broken Japanese voices from UI, keep only Nanami.
# ---------------------------------------------------------------------
if tts_voices_path.exists():
    text = tts_voices_path.read_text(encoding="utf-8-sig")

    marker = "# Keep only verified Japanese voice in this local project environment."
    if marker not in text:
        insert_before = "\n\ndef get_voice_display_name"
        filter_block = f'''
{marker}
EDGE_TTS_VOICES = [
    voice for voice in EDGE_TTS_VOICES
    if not voice["id"].startswith("ja-JP-") or voice["id"] == "ja-JP-NanamiNeural"
]
'''
        if insert_before in text:
            text = text.replace(insert_before, filter_block + insert_before, 1)
        else:
            text += "\n" + filter_block + "\n"

    tts_voices_path.write_text(text, encoding="utf-8")
    print("hidden unsupported Japanese voices from UI")

# ---------------------------------------------------------------------
# 4. Make subtitle splitting stricter:
#    smaller chunks, auto line break, no overflow.
# ---------------------------------------------------------------------
text = asset_path.read_text(encoding="utf-8-sig")

split_pattern = r'''    def _split_full_narration_for_subtitles\(self, text: str, max_chars: int = .*?\) -> List\[str\]:
        .*?
        return \[chunk for chunk in chunks if chunk\]
'''
split_replacement = r'''    def _split_full_narration_for_subtitles(self, text: str, max_chars: int = 22) -> List[str]:
        """
        Split full narration into safe subtitle chunks.

        Rules:
        - Keep every subtitle short enough to stay inside screen.
        - Prefer punctuation split.
        - Wrap long subtitle into two lines.
        """
        import re

        text = self._clean_full_narration_text(text)
        if not text:
            return []

        max_chars = max(12, min(int(max_chars or 22), 24))
        rough_parts = re.split(r"(?<=[\u3002\uff01\uff1f!?])", text)
        chunks: List[str] = []

        for part in rough_parts:
            part = part.strip()
            if not part:
                continue

            while len(part) > max_chars:
                cut = max_chars

                # Prefer Japanese comma or normal comma before hard cutting.
                for sep in ("\u3001", "\uff0c", ","):
                    pos = part.rfind(sep, 0, max_chars + 1)
                    if pos >= 6:
                        cut = pos + 1
                        break

                chunk = part[:cut].strip()
                if chunk:
                    chunks.append(self._wrap_subtitle_chunk(chunk))
                part = part[cut:].strip()

            if part:
                chunks.append(self._wrap_subtitle_chunk(part))

        return [chunk for chunk in chunks if chunk]

    def _wrap_subtitle_chunk(self, text: str, line_limit: int = 11) -> str:
        """
        Wrap one subtitle chunk into one or two lines.
        This prevents long Japanese subtitles from running outside the screen.
        """
        text = (text or "").strip()
        line_limit = max(8, min(int(line_limit or 11), 13))

        if len(text) <= line_limit:
            return text

        if len(text) <= line_limit * 2:
            split_at = len(text) // 2

            # Prefer punctuation around the center.
            candidates = []
            for sep in ("\u3001", "\uff0c", ","):
                pos = text.rfind(sep, 0, split_at + 3)
                if pos >= 5:
                    candidates.append(pos + 1)

            if candidates:
                split_at = candidates[-1]

            return text[:split_at].strip() + "\n" + text[split_at:].strip()

        first = text[:line_limit].strip()
        second = text[line_limit:line_limit * 2].strip()
        return first + "\n" + second
'''

text, count = re.subn(split_pattern, lambda m: split_replacement, text, flags=re.S)
print(f"patched subtitle splitting: {count}")

# ---------------------------------------------------------------------
# 5. Rewrite SRT timing to use safer chunks.
# ---------------------------------------------------------------------
srt_pattern = r'''    def _write_srt_from_full_narration\(self, text: str, total_duration: float, output_path: str\) -> str:
        .*?
        return output_path
'''
srt_replacement = r'''    def _write_srt_from_full_narration(self, text: str, total_duration: float, output_path: str) -> str:
        """
        Generate SRT subtitles from full_narration.
        Subtitle timing follows the full narration duration, not visual scenes.
        """
        chunks = self._split_full_narration_for_subtitles(text, max_chars=22)
        total_duration = max(float(total_duration or 1.0), 1.0)

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        if not chunks:
            Path(output_path).write_text("", encoding="utf-8")
            return output_path

        weights = [max(len(chunk.replace("\n", "")), 1) for chunk in chunks]
        total_weight = sum(weights)
        current = 0.0
        lines = []

        for index, (chunk, weight) in enumerate(zip(chunks, weights), start=1):
            if index == len(chunks):
                end = total_duration
            else:
                duration = total_duration * weight / total_weight
                duration = max(0.9, duration)
                end = min(total_duration, current + duration)

            if end <= current:
                end = min(total_duration, current + 0.8)

            lines.append(str(index))
            lines.append(f"{self._format_srt_time(current)} --> {self._format_srt_time(end)}")
            lines.append(chunk)
            lines.append("")

            current = end

        Path(output_path).write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"Wrote full narration subtitles: {output_path}, chunks={len(chunks)}")
        return output_path
'''

text, count = re.subn(srt_pattern, lambda m: srt_replacement, text, flags=re.S)
print(f"patched SRT writer: {count}")

# ---------------------------------------------------------------------
# 6. Rewrite subtitle burning:
#    dynamic font size, bottom area but not too close to edge.
# ---------------------------------------------------------------------
burn_pattern = r'''    def _burn_subtitles\(self, video_path: str, srt_path: str, output_path: str\) -> str:
        .*?
            return output_path
'''
burn_replacement = r'''    def _burn_subtitles(self, video_path: str, srt_path: str, output_path: str) -> str:
        """Burn SRT subtitles into the final video with dynamic safe sizing."""
        import subprocess
        import shutil

        def probe_size(path: str) -> tuple[int, int]:
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
                path,
            ]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                raw = result.stdout.strip()
                width, height = raw.split("x")
                return int(width), int(height)
            except Exception:
                return 1080, 1920

        width, height = probe_size(video_path)

        # Dynamic font size. For 1080x1920, this is about 12px,
        # roughly half of the previous 22px.
        font_size = max(10, min(13, int(width / 90)))

        # Bottom area, but not glued to the bottom edge.
        # 0.18 means subtitle baseline is around lower 75%-82% visual area.
        margin_v = max(220, min(420, int(height * 0.18)))
        margin_lr = max(60, int(width * 0.07))

        subtitle_path = self._escape_subtitle_filter_path(srt_path)
        force_style = (
            "FontName=Meiryo,"
            f"Fontsize={font_size},"
            "PrimaryColour=&HFFFFFF&,"
            "OutlineColour=&H99000000&,"
            "BackColour=&H66000000&,"
            "BorderStyle=1,"
            "Outline=1.4,"
            "Shadow=0.5,"
            "Alignment=2,"
            f"MarginL={margin_lr},"
            f"MarginR={margin_lr},"
            f"MarginV={margin_v},"
            "WrapStyle=2,"
            "ScaledBorderAndShadow=yes"
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
            logger.info(
                f"Burned subtitles into final video: {output_path}, "
                f"font_size={font_size}, margin_v={margin_v}, margin_lr={margin_lr}"
            )
            return output_path
        except subprocess.CalledProcessError as exc:
            logger.warning(f"Subtitle burn failed, fallback to no subtitles: {exc.stderr}")
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(video_path, output_path)
            return output_path
'''

text, count = re.subn(burn_pattern, lambda m: burn_replacement, text, flags=re.S)
print(f"patched subtitle burning: {count}")

asset_path.write_text(text, encoding="utf-8")

# ---------------------------------------------------------------------
# 7. Prompt: make narration less robotic.
# ---------------------------------------------------------------------
if prompt_path.exists():
    prompt = prompt_path.read_text(encoding="utf-8-sig")
    if "Do not write like a news announcer or a machine-read script." not in prompt:
        target = "- It must be suitable for a natural female Japanese TTS voice at the selected speed.\n"
        insert = (
            "- It must be suitable for a natural female Japanese TTS voice at the selected speed.\n"
            "- Do not write like a news announcer or a machine-read script.\n"
            "- Use plain, soft, conversational Japanese with natural pauses.\n"
            "- Prefer short phrases that sound easy to speak aloud.\n"
        )
        if target in prompt:
            prompt = prompt.replace(target, insert, 1)
        prompt_path.write_text(prompt, encoding="utf-8")
        print("patched narration prompt tone")

print("voice and subtitle optimization applied.")
