# Copyright (C) 2025 AIDC-AI
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Asset-Based Pipeline UI

Implements the UI for generating videos from user-provided assets.
"""

import os
import time
from pathlib import Path
from typing import Any

import streamlit as st
from loguru import logger

from web.i18n import tr, get_language
from web.pipelines.base import PipelineUI, register_pipeline_ui
from web.pipelines.api_workflows import (
    list_api_media_workflows,
    render_api_video_controls,
    workflow_select_help,
    workflow_source_help,
    workflow_source_label,
)
from web.components.content_input import render_bgm_section, render_version_info
from web.utils.async_helpers import run_async
from web.utils.streamlit_helpers import check_and_warn_selfhost_workflow
from web.utils.asset_batch import (
    asset_names,
    build_batch_id,
    build_batch_item_intent,
    choose_narrative_angle,
    copy_video_to_batch_dir,
    create_batch_export_dir,
    evaluate_narration_difference,
    extract_opening_text,
    make_batch_rng,
    parse_narrative_angles,
    select_batch_assets,
    select_batch_assets_with_overlap_limit,
    split_assets_by_type,
    validate_batch_selection,
    write_batch_manifest,
)
from pixelle_video.config import config_manager
from pixelle_video.models.progress import ProgressEvent


class AssetBasedPipelineUI(PipelineUI):
    """
    UI for the Asset-Based Video Generation Pipeline.
    Generates videos from user-provided assets (images/videos).
    """
    name = "custom_media"
    icon = "🎨"
    
    @property
    def display_name(self):
        return tr("pipeline.custom_media.name")
    
    @property
    def description(self):
        return tr("pipeline.custom_media.description")
    
    def render(self, pixelle_video: Any):
        # Three-column layout
        left_col, middle_col, right_col = st.columns([1, 1, 1])
        
        # ====================================================================
        # Left Column: Asset Upload & Video Info
        # ====================================================================
        with left_col:
            asset_params = self._render_asset_input()
            bgm_params = render_bgm_section(key_prefix="asset_")
            render_version_info()
        
        # ====================================================================
        # Middle Column: Video Configuration
        # ====================================================================
        with middle_col:
            config_params = self._render_video_config(pixelle_video, asset_params)
            batch_params = self._render_batch_config(asset_params)
        
        # ====================================================================
        # Right Column: Output Preview
        # ====================================================================
        with right_col:
            # Combine all parameters
            video_params = {
                "pipeline": self.name,
                **asset_params,
                **bgm_params,
                **config_params,
                **batch_params
            }
            
            self._render_output_preview(pixelle_video, video_params)
    
    def _render_asset_input(self) -> dict:
        """Render asset upload section"""
        with st.container(border=True):
            st.markdown(f"**{tr('asset_based.section.assets')}**")
            
            with st.expander(tr("help.feature_description"), expanded=False):
                st.markdown(f"**{tr('help.what')}**")
                st.markdown(tr("asset_based.assets.what"))
                st.markdown(f"**{tr('help.how')}**")
                st.markdown(tr("asset_based.assets.how"))
            
            # File uploader for multiple files
            uploaded_files = st.file_uploader(
                tr("asset_based.assets.upload"),
                type=["jpg", "jpeg", "png", "gif", "webp", "mp4", "mov", "avi", "mkv", "webm"],
                accept_multiple_files=True,
                help=tr("asset_based.assets.upload_help"),
                key="asset_files"
            )
            
            # Save uploaded files to temp directory with unique session ID
            asset_paths = []
            if uploaded_files:
                import uuid
                session_id = str(uuid.uuid4()).replace('-', '')[:12]
                temp_dir = Path(f"temp/assets_{session_id}")
                temp_dir.mkdir(parents=True, exist_ok=True)
                
                for uploaded_file in uploaded_files:
                    file_path = temp_dir / uploaded_file.name
                    with open(file_path, "wb") as f:
                        f.write(uploaded_file.getbuffer())
                    asset_paths.append(str(file_path.absolute()))
                
                st.success(tr("asset_based.assets.count", count=len(asset_paths)))
                
                # Preview uploaded assets
                with st.expander(tr("asset_based.assets.preview"), expanded=True):
                    # Show in a grid (3 columns)
                    cols = st.columns(3)
                    for i, (file, path) in enumerate(zip(uploaded_files, asset_paths)):
                        with cols[i % 3]:
                            # Check if image or video
                            ext = Path(path).suffix.lower()
                            if ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
                                st.image(file, caption=file.name, use_container_width=True)
                            elif ext in [".mp4", ".mov", ".avi", ".mkv", ".webm"]:
                                st.video(file)
                                st.caption(file.name)
            else:
                st.info(tr("asset_based.assets.empty_hint"))
        
        # Video title & intent
        with st.container(border=True):
            st.markdown(f"**{tr('asset_based.section.video_info')}**")
            
            video_title = st.text_input(
                tr("asset_based.video_title"),
                placeholder=tr("asset_based.video_title_placeholder"),
                help=tr("asset_based.video_title_help"),
                key="asset_video_title"
            )
            
            intent = st.text_area(
                tr("asset_based.intent"),
                placeholder=tr("asset_based.intent_placeholder"),
                help=tr("asset_based.intent_help"),
                height=100,
                key="asset_intent"
            )
        
        return {
            "assets": asset_paths,
            "video_title": video_title,
            "intent": intent if intent else None
        }

    def _render_batch_config(self, asset_params: dict | None = None) -> dict:
        """Render batch generation controls for custom media."""
        asset_paths = (asset_params or {}).get("assets") or []
        image_assets, video_assets = split_assets_by_type(asset_paths)

        with st.container(border=True):
            st.markdown("**\u6279\u91cf\u5236\u4f5c**")

            batch_enabled = st.checkbox(
                "\u542f\u7528\u6279\u91cf\u5236\u4f5c",
                value=False,
                key="asset_batch_enabled",
                help="\u4ece\u4e0a\u4f20\u7d20\u6750\u5e93\u91cc\u968f\u673a\u62bd\u53d6\u56fe\u7247\u548c\u89c6\u9891\uff0c\u4e32\u884c\u751f\u6210\u591a\u6761\u4e0d\u540c\u6587\u6848\u7684\u89c6\u9891\u3002",
            )

            st.caption(
                f"\u5df2\u8bc6\u522b\u56fe\u7247\u7d20\u6750\uff1a{len(image_assets)} \u4e2a\uff1b"
                f"\u89c6\u9891\u7d20\u6750\uff1a{len(video_assets)} \u4e2a"
            )

            default_export_dir = str((Path.cwd() / "output" / "batch_exports").resolve())

            if batch_enabled:
                count_col_1, count_col_2 = st.columns([1, 1])

                with count_col_1:
                    random_image_count = st.number_input(
                        "\u968f\u673a\u56fe\u7247\u9009\u62e9\u6570\u91cf",
                        min_value=0,
                        max_value=max(len(image_assets), 0),
                        value=min(3, len(image_assets)) if image_assets else 0,
                        step=1,
                        key="asset_batch_image_count",
                    )

                with count_col_2:
                    random_video_count = st.number_input(
                        "\u968f\u673a\u89c6\u9891\u9009\u62e9\u6570\u91cf",
                        min_value=0,
                        max_value=max(len(video_assets), 0),
                        value=min(2, len(video_assets)) if video_assets else 0,
                        step=1,
                        key="asset_batch_video_count",
                    )

                batch_count = st.number_input(
                    "\u89c6\u9891\u5236\u4f5c\u6570\u91cf",
                    min_value=1,
                    max_value=10,
                    value=3,
                    step=1,
                    key="asset_batch_count",
                    help="\u7b2c\u4e00\u7248\u9650\u5236 1~10 \u6761\uff0c\u4e32\u884c\u751f\u6210\uff0c\u964d\u4f4e TTS\u3001FFmpeg \u548c API \u51b2\u7a81\u98ce\u9669\u3002",
                )

                export_dir = st.text_input(
                    "\u6279\u91cf\u5bfc\u51fa\u6587\u4ef6\u5939\u8def\u5f84",
                    value=default_export_dir,
                    key="asset_batch_export_dir",
                    help="\u672c\u5730 Streamlit \u4f1a\u628a\u6240\u6709\u6210\u529f\u89c6\u9891\u590d\u5236\u5230\u8be5\u6587\u4ef6\u5939\u4e0b\u7684\u6279\u6b21\u5b50\u76ee\u5f55\u3002",
                )

                with st.expander("\u5dee\u5f02\u5316\u539f\u521b\u63a7\u5236", expanded=True):
                    max_material_overlap_percent = st.slider(
                        "\u6700\u5927\u7d20\u6750\u91cd\u53e0\u7387",
                        min_value=0,
                        max_value=100,
                        value=40,
                        step=5,
                        key="asset_batch_max_material_overlap_percent",
                        help="\u65b0\u89c6\u9891\u4e0e\u5df2\u751f\u6210\u89c6\u9891\u7684\u7d20\u6750\u91cd\u53e0\u7387\u8d85\u8fc7\u8be5\u503c\u65f6\uff0c\u4f1a\u5c1d\u8bd5\u91cd\u65b0\u62bd\u53d6\u3002",
                    )

                    max_narration_similarity_percent = st.slider(
                        "\u6700\u5927\u65c1\u767d\u6587\u672c\u76f8\u4f3c\u5ea6",
                        min_value=30,
                        max_value=95,
                        value=70,
                        step=5,
                        key="asset_batch_max_narration_similarity_percent",
                        help="\u65b0 full_narration \u4e0e\u5df2\u751f\u6210\u65c1\u767d\u8d85\u8fc7\u8be5\u76f8\u4f3c\u5ea6\u65f6\uff0c\u4f1a\u91cd\u8bd5\u672c\u6761\u751f\u6210\u3002",
                    )

                    max_opening_similarity_percent = st.slider(
                        "\u6700\u5927\u5f00\u5934\u76f8\u4f3c\u5ea6",
                        min_value=30,
                        max_value=95,
                        value=65,
                        step=5,
                        key="asset_batch_max_opening_similarity_percent",
                        help="\u68c0\u67e5\u6bcf\u6761\u65c1\u767d\u5f00\u5934\u662f\u5426\u592a\u50cf\u3002",
                    )

                    similarity_retry_count = st.number_input(
                        "\u76f8\u4f3c\u5ea6\u8fc7\u9ad8\u65f6\u91cd\u8bd5\u6b21\u6570",
                        min_value=0,
                        max_value=3,
                        value=2,
                        step=1,
                        key="asset_batch_similarity_retry_count",
                        help="\u91cd\u8bd5\u4f1a\u91cd\u65b0\u751f\u6210\u672c\u6761\u89c6\u9891\uff0c\u66f4\u7a33\u4f46\u66f4\u8017\u65f6\u3002",
                    )

                    default_angles = "\n".join([
                        "\u4e00\u4e2a\u4eba\u5403\u996d\u65f6\u7684\u5b89\u9759\u548c\u5c0f\u5c0f\u5bc2\u5bde",
                        "\u4e0b\u96e8\u5929\u56de\u5bb6\u8def\u4e0a\u60f3\u6709\u4eba\u966a\u7684\u611f\u89c9",
                        "\u5468\u672b\u6563\u6b65\u65f6\u5bf9\u7a33\u5b9a\u5173\u7cfb\u7684\u671f\u5f85",
                        "\u5de5\u4f5c\u7ed3\u675f\u540e\u60f3\u548c\u6210\u719f\u7537\u6027\u5e73\u9759\u8bf4\u8bdd",
                        "\u505a\u996d\u548c\u6536\u62fe\u623f\u95f4\u65f6\u611f\u5230\u4e00\u4e2a\u4eba\u7684\u4e0d\u4fbf",
                        "\u591c\u665a\u5b89\u9759\u65f6\u5e0c\u671b\u672a\u6765\u6709\u4e00\u4e2a\u6e29\u6696\u7684\u4eba",
                        "\u60f3\u9047\u5230\u4e0d\u6025\u8e81\u3001\u613f\u610f\u6162\u6162\u4e86\u89e3\u7684\u4eba",
                        "\u5bf9\u4e24\u4e2a\u4eba\u4e00\u8d77\u5b89\u7a33\u8d70\u4e0b\u53bb\u7684\u671f\u5f85",
                    ])

                    narrative_angles_text = st.text_area(
                        "\u53d9\u4e8b\u89d2\u5ea6\u6c60\uff08\u6bcf\u884c\u4e00\u4e2a\uff09",
                        value=default_angles,
                        height=180,
                        key="asset_batch_narrative_angles_text",
                        help="\u6279\u91cf\u751f\u6210\u65f6\uff0c\u6bcf\u6761\u89c6\u9891\u4f1a\u5206\u914d\u4e00\u4e2a\u53d9\u4e8b\u89d2\u5ea6\uff0c\u7528\u6765\u62c9\u5f00\u6587\u6848\u5dee\u5f02\u3002",
                    )

                st.caption(
                    "\u89c4\u5219\uff1a\u5355\u4e2a\u89c6\u9891\u5185\u4e0d\u91cd\u590d\u4f7f\u7528\u540c\u4e00\u7d20\u6750\uff1b"
                    "\u4e0d\u540c\u89c6\u9891\u4e4b\u95f4\u5141\u8bb8\u590d\u7528\u7d20\u6750\uff1b"
                    "\u4f46\u4f1a\u5c3d\u91cf\u63a7\u5236\u6279\u6b21\u5185\u7684\u7d20\u6750\u91cd\u53e0\u7387\u3002"
                )
            else:
                random_image_count = 0
                random_video_count = 0
                batch_count = 1
                export_dir = default_export_dir
                max_material_overlap_percent = 40
                max_narration_similarity_percent = 70
                max_opening_similarity_percent = 65
                similarity_retry_count = 2
                narrative_angles_text = ""

        return {
            "batch_enabled": bool(batch_enabled),
            "batch_image_count": int(random_image_count or 0),
            "batch_video_count": int(random_video_count or 0),
            "batch_count": int(batch_count or 1),
            "batch_export_dir": export_dir,
            "batch_max_material_overlap": float(max_material_overlap_percent or 0) / 100.0,
            "batch_max_narration_similarity": float(max_narration_similarity_percent or 0) / 100.0,
            "batch_max_opening_similarity": float(max_opening_similarity_percent or 0) / 100.0,
            "batch_similarity_retry_count": int(similarity_retry_count or 0),
            "batch_narrative_angles_text": narrative_angles_text,
        }

    def _render_video_config(self, pixelle_video: Any, asset_params: dict | None = None) -> dict:
        """Render video configuration section"""
        # Duration configuration
        with st.container(border=True):
            st.markdown(f"**{tr('video.title')}**")
            
            # Duration slider
            duration = st.slider(
                tr("asset_based.duration"),
                min_value=15,
                max_value=120,
                value=30,
                step=5,
                help=tr("asset_based.duration_help"),
                key="asset_duration"
            )
            st.caption(tr("asset_based.duration_label", seconds=duration))
        
        # Workflow source selection
        with st.container(border=True):
            st.markdown(f"**{tr('asset_based.section.source')}**")
            
            with st.expander(tr("help.feature_description"), expanded=False):
                st.markdown(f"**{tr('help.what')}**")
                st.markdown(tr("asset_based.source.what"))
                st.markdown(f"**{tr('help.how')}**")
                st.markdown(tr("asset_based.source.how"))
            
            source_options = {
                "runninghub": tr("asset_based.source.runninghub"),
                "selfhost": tr("asset_based.source.selfhost"),
                "api": "API 调用" if get_language() == "zh_CN" else "API call",
            }
            
            # Check if RunningHub API key is configured
            comfyui_config = config_manager.get_comfyui_config()
            api_provider_config = config_manager.config.to_dict().get("api_providers", {})
            has_runninghub = bool(comfyui_config.get("runninghub_api_key"))
            has_selfhost = bool(comfyui_config.get("comfyui_url"))
            has_api_analysis = any(
                bool((api_provider_config.get(provider, {}) or {}).get("api_key"))
                for provider in ("dashscope", "openai", "gemini")
            )

            asset_paths = (asset_params or {}).get("assets") or []
            image_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
            video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
            has_image_assets = any(Path(path).suffix.lower() in image_exts for path in asset_paths)
            has_video_assets = any(Path(path).suffix.lower() in video_exts for path in asset_paths)

            def analysis_source_available(source_name: str) -> bool:
                source_dir = Path("workflows") / source_name
                image_available = (source_dir / "analyse_image.json").exists()
                video_available = (source_dir / "analyse_video.json").exists()
                if has_image_assets and not image_available:
                    return False
                if has_video_assets and not video_available:
                    return False
                return image_available or video_available
            
            # Prefer API VLM when configured, so API media workflows do not depend on RunningHub.
            source_keys = []
            if analysis_source_available("runninghub"):
                source_keys.append("runninghub")
            if analysis_source_available("selfhost"):
                source_keys.append("selfhost")
            if has_api_analysis:
                source_keys.append("api")
            if not source_keys:
                source_keys = ["runninghub"]

            if has_api_analysis and "api" in source_keys:
                default_source = "api"
            elif has_runninghub and "runninghub" in source_keys:
                default_source = "runninghub"
            elif "selfhost" in source_keys:
                default_source = "selfhost"
            else:
                default_source = source_keys[0]
            default_source_index = source_keys.index(default_source)
            
            if st.session_state.get("asset_source") not in source_keys:
                st.session_state.pop("asset_source", None)

            source = st.radio(
                "素材分析服务" if get_language() == "zh_CN" else "Asset analysis service",
                options=source_keys,
                format_func=lambda x: source_options[x],
                index=default_source_index,
                horizontal=True,
                key="asset_source",
                label_visibility="visible",
                help=workflow_source_help("素材分析" if get_language() == "zh_CN" else "asset analysis"),
            )
            
            # Show hint based on selection
            if source == "api":
                if not has_api_analysis:
                    st.warning(
                        "未配置可用于 VLM 素材分析的 API Key（DashScope/OpenAI/Gemini）。"
                        if get_language() == "zh_CN"
                        else "No API key configured for VLM asset analysis (DashScope/OpenAI/Gemini)."
                    )
                else:
                    st.info(
                        "使用 API VLM 分析上传素材，不依赖 RunningHub/ComfyUI。"
                        if get_language() == "zh_CN"
                        else "Use API VLM to analyze uploaded assets without RunningHub/ComfyUI."
                    )
            elif source == "runninghub":
                if not has_runninghub:
                    st.warning(tr("asset_based.source.runninghub_not_configured"))
                else:
                    st.info(tr("asset_based.source.runninghub_hint"))
            else:
                if not has_selfhost:
                    st.warning(tr("asset_based.source.selfhost_not_configured"))
                else:
                    st.info(tr("asset_based.source.selfhost_hint"))
                    # Check and warn for selfhost mode (auto popup if not confirmed)
                    # Use analyse_image.json as representative workflow
                    check_and_warn_selfhost_workflow("selfhost/analyse_image.json")

            api_video_workflow = None
            api_video_params = {}
            api_video_workflows = list_api_media_workflows(
                pixelle_video,
                "video",
                required_adapter_abilities=["first_frame_i2v"],
                verified_only=True,
            )
            animation_source_options = ["none"]
            if api_video_workflows:
                animation_source_options.append("api")

            if st.session_state.get("asset_animation_source") not in animation_source_options:
                st.session_state.pop("asset_animation_source", None)

            def animation_source_label(value: str) -> str:
                if value == "none":
                    return "不启用" if get_language() == "zh_CN" else "Disabled"
                return workflow_source_label(value)

            animation_source = st.radio(
                "素材动画服务" if get_language() == "zh_CN" else "Asset animation service",
                animation_source_options,
                format_func=animation_source_label,
                horizontal=True,
                key="asset_animation_source",
                help=(
                    "选择是否把匹配到的图片素材动画化。不启用时保留原素材静态合成；API 模型会调用已验证的图生视频模型。"
                    if get_language() == "zh_CN"
                    else "Choose whether to animate matched image assets. Disabled keeps the original static asset composition; API models call verified image-to-video providers."
                ),
            )

            if animation_source == "api":
                animation_workflows = api_video_workflows
            else:
                animation_workflows = []
                st.info(
                    "未启用素材动画，将使用原素材静态合成流程。"
                    if get_language() == "zh_CN"
                    else "Asset animation is disabled; the original static asset composition flow will be used."
                )

            animation_options = [wf["display_name"] for wf in animation_workflows]
            selected_animation = st.selectbox(
                "素材动画工作流/模型" if get_language() == "zh_CN" else "Asset animation workflow/model",
                animation_options if animation_options else ["No workflow/model available"],
                index=0,
                key="asset_animation_workflow",
                disabled=not animation_options,
                help=workflow_select_help(),
            )
            if animation_options and animation_source == "api":
                selected_index = animation_options.index(selected_animation)
                selected_workflow = animation_workflows[selected_index]
                api_video_workflow = selected_workflow["key"]
                api_video_params = render_api_video_controls(
                    selected_workflow,
                    key_prefix="asset",
                    default_duration=5,
                    allow_audio_driven=True,
                    show_duration=False,
                )
        
        # TTS configuration
        with st.container(border=True):
            st.markdown(f"**{tr('section.tts')}**")
            
            # Import voice configuration
            from pixelle_video.tts_voices import EDGE_TTS_VOICES, get_voice_display_name
            
            # Get saved voice from config
            comfyui_config = config_manager.get_comfyui_config()
            tts_config = comfyui_config.get("tts", {})
            local_config = tts_config.get("local", {})
            saved_voice = local_config.get("voice", "ja-JP-NanamiNeural")
            saved_speed = local_config.get("speed", 1.2)
            
            # Build voice options with i18n
            voice_options = []
            voice_ids = []
            default_voice_index = 0
            
            for idx, voice_config in enumerate(EDGE_TTS_VOICES):
                voice_id = voice_config["id"]
                display_name = get_voice_display_name(voice_id, tr, get_language())
                voice_options.append(display_name)
                voice_ids.append(voice_id)
                
                if voice_id == saved_voice:
                    default_voice_index = idx
            
            # Two-column layout
            voice_col, speed_col = st.columns([1, 1])
            
            with voice_col:
                selected_voice_display = st.selectbox(
                    tr("tts.voice_selector"),
                    voice_options,
                    index=default_voice_index,
                    key="asset_tts_voice"
                )
                selected_voice_index = voice_options.index(selected_voice_display)
                voice_id = voice_ids[selected_voice_index]
            
            with speed_col:
                tts_speed = st.slider(
                    tr("tts.speed"),
                    min_value=0.5,
                    max_value=2.0,
                    value=saved_speed,
                    step=0.1,
                    format="%.1fx",
                    key="asset_tts_speed"
                )
                st.caption(tr("tts.speed_label", speed=f"{tts_speed:.1f}"))
        
        return {
            "duration": duration,
            "source": source,
            "api_video_workflow": api_video_workflow,
            "api_video_params": api_video_params,
            "voice_id": voice_id,
            "tts_speed": tts_speed
        }
    
    def _render_output_preview(self, pixelle_video: Any, video_params: dict):
        """Render output preview section"""
        with st.container(border=True):
            st.markdown(f"**{tr('section.video_generation')}**")

            # Check configuration
            if not config_manager.validate():
                st.warning(tr("settings.not_configured"))

            # Check if assets are provided
            assets = video_params.get("assets", [])
            if not assets:
                st.info(tr("asset_based.output.no_assets"))
                st.button(
                    tr("btn.generate"),
                    type="primary",
                    use_container_width=True,
                    disabled=True,
                    key="asset_generate_disabled"
                )
                return

            image_assets, video_assets = split_assets_by_type(assets)

            if video_params.get("batch_enabled"):
                st.info(
                    f"\u6279\u91cf\u7d20\u6750\u5e93\u5df2\u5c31\u7eea\uff1a"
                    f"\u56fe\u7247 {len(image_assets)} \u4e2a\uff0c\u89c6\u9891 {len(video_assets)} \u4e2a"
                )
                self._render_batch_generation(pixelle_video, video_params)
            else:
                # Show asset summary
                st.info(tr("asset_based.output.ready", count=len(assets)))
                self._render_single_generation(pixelle_video, video_params)

    def _format_asset_progress_message(self, event: ProgressEvent) -> str:
        """Format asset pipeline progress messages."""
        if event.event_type == "analyzing_assets":
            if event.extra_info == "start":
                return tr("asset_based.progress.analyzing_start", total=event.frame_total)
            return tr("asset_based.progress.analyzing_complete", count=event.frame_total)

        if event.event_type == "analyzing_asset":
            return tr(
                "asset_based.progress.analyzing_asset",
                current=event.frame_current,
                total=event.frame_total,
                name=event.extra_info or ""
            )

        if event.event_type == "generating_script":
            if event.extra_info == "complete":
                return tr("asset_based.progress.script_complete")
            return tr("asset_based.progress.generating_script")

        if event.event_type == "frame_step":
            action_key = f"progress.step_{event.action}"
            action_text = tr(action_key)
            return tr(
                "progress.frame_step",
                current=event.frame_current,
                total=event.frame_total,
                step=event.step,
                action=action_text
            )

        if event.event_type == "processing_frame":
            return tr(
                "progress.frame",
                current=event.frame_current,
                total=event.frame_total
            )

        if event.event_type == "concatenating":
            if event.extra_info == "complete":
                return tr("asset_based.progress.concat_complete")
            return tr("progress.concatenating")

        if event.event_type == "completed":
            return tr("progress.completed")

        try:
            return tr(f"progress.{event.event_type}")
        except Exception:
            return str(event.event_type)

    def _run_asset_pipeline(
        self,
        pixelle_video: Any,
        video_params: dict,
        assets: list[str],
        intent: str | None,
        progress_callback,
    ):
        """Run one asset-based video generation task."""
        from pixelle_video.pipelines.asset_based import AssetBasedPipeline

        pipeline = AssetBasedPipeline(pixelle_video)
        start_time = time.time()

        ctx = run_async(pipeline(
            assets=assets,
            video_title=video_params.get("video_title", ""),
            intent=intent,
            duration=video_params.get("duration", 30),
            source=video_params.get("source", "runninghub"),
            bgm_path=video_params.get("bgm_path"),
            bgm_volume=video_params.get("bgm_volume", 0.2),
            bgm_mode=video_params.get("bgm_mode", "loop"),
            api_video_workflow=video_params.get("api_video_workflow"),
            api_video_params=video_params.get("api_video_params"),
            voice_id=video_params.get("voice_id", "ja-JP-NanamiNeural"),
            tts_speed=video_params.get("tts_speed", 1.0),
            progress_callback=progress_callback
        ))

        return ctx, time.time() - start_time

    def _render_single_generation(self, pixelle_video: Any, video_params: dict):
        """Render and run the original single-video generation flow."""
        if st.button(tr("btn.generate"), type="primary", use_container_width=True, key="asset_generate"):
            if not config_manager.validate():
                st.error(tr("settings.not_configured"))
                st.stop()

            progress_bar = st.progress(0)
            status_text = st.empty()

            def update_progress(event: ProgressEvent):
                status_text.text(self._format_asset_progress_message(event))
                progress_bar.progress(min(int(event.progress * 100), 99))

            try:
                ctx, total_time = self._run_asset_pipeline(
                    pixelle_video=pixelle_video,
                    video_params=video_params,
                    assets=video_params["assets"],
                    intent=video_params.get("intent"),
                    progress_callback=update_progress,
                )

                progress_bar.progress(100)
                status_text.text(tr("status.success"))
                self._render_single_result(ctx, total_time)

            except Exception as e:
                status_text.text("")
                progress_bar.empty()
                st.error(tr("status.error", error=str(e)))
                logger.exception(e)
                st.stop()

    def _render_single_result(self, ctx: Any, total_time: float):
        """Render one successful generated video."""
        st.success(tr("status.video_generated", path=ctx.final_video_path))
        st.markdown("---")

        if os.path.exists(ctx.final_video_path):
            file_size_mb = os.path.getsize(ctx.final_video_path) / (1024 * 1024)
            n_scenes = len(ctx.storyboard.frames) if ctx.storyboard else 0

            info_text = (
                f"⏱️ {tr('info.generation_time')} {total_time:.1f}s   "
                f"📦 {file_size_mb:.2f}MB   "
                f"🎬 {n_scenes}{tr('info.scenes_unit')}"
            )
            st.caption(info_text)

            st.markdown("---")
            st.video(ctx.final_video_path)

            with open(ctx.final_video_path, "rb") as video_file:
                video_bytes = video_file.read()
                video_filename = os.path.basename(ctx.final_video_path)
                st.download_button(
                    label="⬇️ \u4e0b\u8f7d\u89c6\u9891" if get_language() == "zh_CN" else "⬇️ Download Video",
                    data=video_bytes,
                    file_name=video_filename,
                    mime="video/mp4",
                    use_container_width=True,
                    key=f"asset_single_download_{getattr(ctx, 'task_id', video_filename)}",
                )
        else:
            st.error(tr("status.video_not_found", path=ctx.final_video_path))

    def _render_batch_generation(self, pixelle_video: Any, video_params: dict):
        """Render and run batch video generation."""
        assets = video_params.get("assets") or []
        image_assets, video_assets = split_assets_by_type(assets)

        batch_count = int(video_params.get("batch_count") or 1)
        image_count = int(video_params.get("batch_image_count") or 0)
        video_count = int(video_params.get("batch_video_count") or 0)
        export_dir = str(video_params.get("batch_export_dir") or "").strip()

        max_material_overlap = float(video_params.get("batch_max_material_overlap") or 0.4)
        max_narration_similarity = float(video_params.get("batch_max_narration_similarity") or 0.7)
        max_opening_similarity = float(video_params.get("batch_max_opening_similarity") or 0.65)
        similarity_retry_count = int(video_params.get("batch_similarity_retry_count") or 0)
        narrative_angles = parse_narrative_angles(video_params.get("batch_narrative_angles_text"))

        st.caption(
            f"\u5c06\u4e32\u884c\u5236\u4f5c {batch_count} \u6761\u89c6\u9891\uff1b"
            f"\u6bcf\u6761\u968f\u673a\u62bd\u53d6\u56fe\u7247 {image_count} \u4e2a\u3001"
            f"\u89c6\u9891 {video_count} \u4e2a\u3002"
        )

        st.caption(
            f"\u5dee\u5f02\u5316\u9608\u503c\uff1a\u7d20\u6750\u91cd\u53e0\u7387 \u2264 {max_material_overlap:.0%}\uff0c"
            f"\u65c1\u767d\u76f8\u4f3c\u5ea6 \u2264 {max_narration_similarity:.0%}\uff0c"
            f"\u5f00\u5934\u76f8\u4f3c\u5ea6 \u2264 {max_opening_similarity:.0%}"
        )

        if st.button(
            "\u5f00\u59cb\u6279\u91cf\u5236\u4f5c\u5e76\u4fdd\u5b58\u5230\u6307\u5b9a\u6587\u4ef6\u5939",
            type="primary",
            use_container_width=True,
            key="asset_batch_generate",
        ):
            if not config_manager.validate():
                st.error(tr("settings.not_configured"))
                st.stop()

            try:
                validate_batch_selection(
                    image_assets=image_assets,
                    video_assets=video_assets,
                    image_count=image_count,
                    video_count=video_count,
                    batch_count=batch_count,
                )

                batch_id = build_batch_id()
                batch_dir = create_batch_export_dir(export_dir, batch_id)
                batch_id = batch_dir.name

            except Exception as e:
                st.error(f"\u6279\u91cf\u53c2\u6570\u65e0\u6548\uff1a{e}")
                st.stop()

            progress_bar = st.progress(0)
            status_text = st.empty()
            rng = make_batch_rng(batch_id)

            previous_asset_sets: list[list[str]] = []
            previous_narrations: list[str] = []

            manifest = {
                "batch_id": batch_id,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "completed_at": None,
                "title": video_params.get("video_title", ""),
                "intent": video_params.get("intent"),
                "target_duration": video_params.get("duration", 30),
                "voice_id": video_params.get("voice_id", "ja-JP-NanamiNeural"),
                "tts_speed": video_params.get("tts_speed", 1.0),
                "source": video_params.get("source", "runninghub"),
                "export_dir": str(batch_dir.resolve()),
                "requested_count": batch_count,
                "random_image_count": image_count,
                "random_video_count": video_count,
                "max_material_overlap": max_material_overlap,
                "max_narration_similarity": max_narration_similarity,
                "max_opening_similarity": max_opening_similarity,
                "similarity_retry_count": similarity_retry_count,
                "narrative_angles": narrative_angles,
                "success_count": 0,
                "failed_count": 0,
                "items": [],
            }

            for index in range(1, batch_count + 1):
                selected_assets: list[str] = []
                selected_images: list[str] = []
                selected_videos: list[str] = []
                material_overlap = 0.0
                material_overlap_passed = True
                narrative_angle = choose_narrative_angle(narrative_angles, index, rng)

                record = {
                    "index": index,
                    "status": "running",
                    "attempt_count": 0,
                    "narrative_angle": narrative_angle,
                    "selected_images": [],
                    "selected_videos": [],
                    "selected_image_names": [],
                    "selected_video_names": [],
                    "selected_assets": [],
                    "source_output_path": None,
                    "export_path": None,
                    "generation_time_seconds": None,
                    "file_size_bytes": None,
                    "n_scenes": None,
                    "full_narration": None,
                    "opening_text": None,
                    "material_overlap_max": None,
                    "material_overlap_passed": None,
                    "narration_similarity_max": None,
                    "opening_similarity_max": None,
                    "differentiation_warnings": [],
                    "attempts": [],
                    "error": None,
                }

                try:
                    for attempt in range(1, similarity_retry_count + 2):
                        selected_assets, selected_images, selected_videos, material_overlap, material_overlap_passed = (
                            select_batch_assets_with_overlap_limit(
                                image_assets=image_assets,
                                video_assets=video_assets,
                                image_count=image_count,
                                video_count=video_count,
                                rng=rng,
                                previous_asset_sets=previous_asset_sets,
                                max_overlap=max_material_overlap,
                                max_attempts=30,
                            )
                        )

                        status_text.text(
                            f"\u6279\u91cf {index}/{batch_count}\uff1a"
                            f"\u7b2c {attempt} \u6b21\u5c1d\u8bd5\uff0c\u51c6\u5907\u751f\u6210"
                        )

                        variant_seed = f"{batch_id}-{index}-{attempt}-{rng.randrange(1000000, 9999999)}"
                        previous_openings = [
                            extract_opening_text(text)
                            for text in previous_narrations
                            if str(text).strip()
                        ]

                        item_intent = build_batch_item_intent(
                            base_intent=video_params.get("intent"),
                            title=video_params.get("video_title", ""),
                            index=index,
                            total=batch_count,
                            selected_assets=selected_assets,
                            variant_seed=variant_seed,
                            narrative_angle=narrative_angle,
                            previous_narrations=previous_narrations,
                            previous_openings=previous_openings,
                        )

                        def update_progress(event: ProgressEvent, item_index: int = index):
                            message = self._format_asset_progress_message(event)
                            overall_progress = ((item_index - 1) + float(event.progress or 0)) / batch_count
                            status_text.text(f"\u6279\u91cf {item_index}/{batch_count}\uff1a{message}")
                            progress_bar.progress(min(int(overall_progress * 100), 99))

                        ctx, total_time = self._run_asset_pipeline(
                            pixelle_video=pixelle_video,
                            video_params=video_params,
                            assets=selected_assets,
                            intent=item_intent,
                            progress_callback=update_progress,
                        )

                        full_narration = getattr(ctx, "full_narration", "") or ""

                        difference_report = evaluate_narration_difference(
                            narration=full_narration,
                            previous_narrations=previous_narrations,
                            max_narration_similarity=max_narration_similarity,
                            max_opening_similarity=max_opening_similarity,
                        )

                        attempt_warnings = list(difference_report.get("warnings") or [])
                        if not material_overlap_passed:
                            attempt_warnings.append(
                                f"material overlap too high after retries: {material_overlap:.3f} > {max_material_overlap:.3f}"
                            )

                        record["attempts"].append({
                            "attempt": attempt,
                            "selected_image_names": asset_names(selected_images),
                            "selected_video_names": asset_names(selected_videos),
                            "material_overlap_max": round(float(material_overlap), 4),
                            "material_overlap_passed": bool(material_overlap_passed),
                            "narration_similarity_max": difference_report["narration_similarity_max"],
                            "opening_similarity_max": difference_report["opening_similarity_max"],
                            "opening_text": difference_report["opening_text"],
                            "warnings": attempt_warnings,
                            "source_output_path": getattr(ctx, "final_video_path", None),
                        })

                        should_retry = bool(attempt_warnings) and attempt <= similarity_retry_count
                        if should_retry:
                            logger.warning(
                                f"Batch item {index} attempt {attempt} is too similar, retrying: {attempt_warnings}"
                            )
                            continue

                        export_path = copy_video_to_batch_dir(
                            source_video_path=ctx.final_video_path,
                            batch_dir=batch_dir,
                            video_title=video_params.get("video_title", ""),
                            index=index,
                        )

                        file_size = os.path.getsize(export_path) if os.path.exists(export_path) else 0
                        n_scenes = len(ctx.storyboard.frames) if ctx.storyboard else 0

                        record.update({
                            "status": "success",
                            "attempt_count": attempt,
                            "selected_images": selected_images,
                            "selected_videos": selected_videos,
                            "selected_image_names": asset_names(selected_images),
                            "selected_video_names": asset_names(selected_videos),
                            "selected_assets": selected_assets,
                            "source_output_path": ctx.final_video_path,
                            "export_path": export_path,
                            "generation_time_seconds": round(total_time, 2),
                            "file_size_bytes": file_size,
                            "n_scenes": n_scenes,
                            "full_narration": full_narration,
                            "opening_text": difference_report["opening_text"],
                            "material_overlap_max": round(float(material_overlap), 4),
                            "material_overlap_passed": bool(material_overlap_passed),
                            "narration_similarity_max": difference_report["narration_similarity_max"],
                            "opening_similarity_max": difference_report["opening_similarity_max"],
                            "differentiation_warnings": attempt_warnings,
                            "error": None,
                        })

                        previous_asset_sets.append(list(selected_assets))
                        previous_narrations.append(full_narration)
                        break

                except Exception as e:
                    record.update({
                        "status": "failed",
                        "error": str(e),
                    })
                    logger.exception(e)

                manifest["items"].append(record)
                manifest["success_count"] = sum(1 for item in manifest["items"] if item["status"] == "success")
                manifest["failed_count"] = sum(1 for item in manifest["items"] if item["status"] == "failed")
                write_batch_manifest(batch_dir, manifest)

            manifest["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            write_batch_manifest(batch_dir, manifest)

            progress_bar.progress(100)
            status_text.text("\u6279\u91cf\u5236\u4f5c\u5b8c\u6210")
            st.session_state["asset_batch_results"] = manifest

            st.success(
                f"\u6279\u91cf\u5b8c\u6210\uff1a\u6210\u529f {manifest['success_count']} \u6761\uff0c"
                f"\u5931\u8d25 {manifest['failed_count']} \u6761\u3002"
            )

        batch_results = st.session_state.get("asset_batch_results")
        if batch_results:
            self._render_batch_results(batch_results)

    def _render_batch_results(self, manifest: dict):
        """Render batch result list with preview, per-video download, and failure reason."""
        items = manifest.get("items") or []
        if not items:
            return

        st.markdown("---")
        st.markdown("**\u6279\u91cf\u7ed3\u679c**")
        st.caption(f"\u5bfc\u51fa\u6587\u4ef6\u5939\uff1a{manifest.get('export_dir', '')}")

        manifest_path = manifest.get("manifest_path")
        if manifest_path:
            st.caption(f"\u6279\u91cf\u8bb0\u5f55\uff1a{manifest_path}")

        for item in items:
            index = item.get("index")
            status = item.get("status")
            status_label = "\u6210\u529f" if status == "success" else "\u5931\u8d25"

            with st.expander(f"\u7b2c {index} \u6761 - {status_label}", expanded=status != "success"):
                image_names = item.get("selected_image_names") or []
                video_names = item.get("selected_video_names") or []

                if image_names:
                    st.caption("\u4f7f\u7528\u56fe\u7247\uff1a" + " / ".join(image_names))

                if video_names:
                    st.caption("\u4f7f\u7528\u89c6\u9891\uff1a" + " / ".join(video_names))

                if status == "success":
                    export_path = item.get("export_path") or item.get("source_output_path")

                    if export_path and os.path.exists(export_path):
                        file_size_mb = os.path.getsize(export_path) / (1024 * 1024)
                        generation_time = item.get("generation_time_seconds")
                        n_scenes = item.get("n_scenes")

                        st.caption(
                            f"⏱️ {generation_time}s   "
                            f"📦 {file_size_mb:.2f}MB   "
                            f"🎬 {n_scenes}{tr('info.scenes_unit')}"
                        )

                        st.video(export_path)

                        with open(export_path, "rb") as video_file:
                            st.download_button(
                                label=f"⬇️ \u4e0b\u8f7d\u7b2c {index} \u6761\u89c6\u9891",
                                data=video_file.read(),
                                file_name=os.path.basename(export_path),
                                mime="video/mp4",
                                use_container_width=True,
                                key=f"asset_batch_download_{manifest.get('batch_id')}_{index}",
                            )
                    else:
                        st.error(f"\u89c6\u9891\u6587\u4ef6\u4e0d\u5b58\u5728\uff1a{export_path}")
                else:
                    st.error(item.get("error") or "\u672a\u77e5\u9519\u8bef")


# Register self
register_pipeline_ui(AssetBasedPipelineUI)
