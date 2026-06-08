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
Asset-Based Video Pipeline

Generates marketing videos from user-provided assets (images/videos) rather than
AI-generated media. Ideal for small businesses with existing media libraries.

Workflow:
1. Analyze uploaded assets (images/videos)
2. Generate script based on user intent and available assets
3. Match assets to script scenes
4. Compose final video with narrations

Example:
    pipeline = AssetBasedPipeline(pixelle_video)
    result = await pipeline(
        assets=["/path/img1.jpg", "/path/img2.jpg"],
        video_title="Pet Store Year-End Sale",
        intent="Promote our pet store's year-end sale with a warm and friendly tone",
        duration=30
    )
"""

from typing import List, Dict, Any, Optional, Callable
from pathlib import Path
import math
import random
import json
from datetime import datetime

from loguru import logger
from pydantic import BaseModel, Field

from pixelle_video.pipelines.linear import LinearVideoPipeline, PipelineContext
from pixelle_video.models.progress import ProgressEvent
from pixelle_video.utils.os_util import (
    create_task_output_dir,
    get_task_final_video_path,
    get_task_frame_path,
)

# Type alias for progress callback
ProgressCallback = Optional[Callable[[ProgressEvent], None]]


# ==================== Structured Output Models ====================

class SceneScript(BaseModel):
    """Single scene in the video script"""
    scene_number: int = Field(description="Scene number starting from 1")
    asset_path: str = Field(description="Path to the asset file for this scene")
    narrations: List[str] = Field(description="List of narration sentences for this scene (1-5 sentences)")
    duration: int = Field(description="Estimated duration in seconds for this scene")


class VideoScript(BaseModel):
    """Complete video script with scenes"""
    full_narration: Optional[str] = Field(
        default=None,
        description="One continuous natural Japanese narration for the whole video"
    )
    scenes: List[SceneScript] = Field(description="List of scenes in the video")


class AssetBasedPipeline(LinearVideoPipeline):
    """
    Asset-Based Video Pipeline
    
    Generates videos from user-provided assets instead of AI-generated media.
    """
    
    def __init__(self, core):
        """
        Initialize pipeline
        
        Args:
            core: PixelleVideoCore instance
        """
        super().__init__(core)
        self.asset_index: Dict[str, Any] = {}  # In-memory asset metadata
    
    async def __call__(
        self,
        assets: List[str],
        video_title: str = "",
        intent: Optional[str] = None,
        duration: int = 30,
        source: str = "runninghub",
        bgm_path: Optional[str] = None,
        bgm_volume: float = 0.2,
        bgm_mode: str = "loop",
        progress_callback: ProgressCallback = None,
        **kwargs
    ) -> PipelineContext:
        """
        Execute pipeline with user-provided assets
        
        Args:
            assets: List of asset file paths
            video_title: Video title
            intent: Video intent/purpose (defaults to video_title)
            duration: Target duration in seconds
            source: Workflow source ("runninghub" or "selfhost")
            bgm_path: Path to background music file (optional)
            bgm_volume: BGM volume (0.0-1.0, default 0.2)
            bgm_mode: BGM mode ("loop" or "once", default "loop")
            progress_callback: Optional callback for progress updates
            **kwargs: Additional parameters
        
        Returns:
            Pipeline context with generated video
        """
        from pixelle_video.pipelines.linear import PipelineContext
        
        # Store progress callback
        self._progress_callback = progress_callback
        
        # Create custom context with asset-specific parameters
        ctx = PipelineContext(
            input_text=intent or video_title,  # Use intent or title as input_text
            params={
                "assets": assets,
                "video_title": video_title,
                "intent": intent or video_title,
                "duration": duration,
                "source": source,
                "bgm_path": bgm_path,
                "bgm_volume": bgm_volume,
                "bgm_mode": bgm_mode,
                **kwargs
            }
        )
        
        # Store request parameters in context for easy access
        ctx.request = ctx.params
        
        try:
            # Execute pipeline lifecycle
            await self.setup_environment(ctx)
            await self.determine_title(ctx)
            await self.generate_content(ctx)
            await self.plan_visuals(ctx)
            await self.initialize_storyboard(ctx)
            await self.produce_assets(ctx)
            await self.post_production(ctx)
            await self.finalize(ctx)
            
            return ctx
            
        except Exception as e:
            await self.handle_exception(ctx, e)
            raise
    
    def _emit_progress(self, event: ProgressEvent):
        """Emit progress event to callback if available"""
        if self._progress_callback:
            self._progress_callback(event)
    
    async def setup_environment(self, context: PipelineContext) -> PipelineContext:
        """
        Analyze uploaded assets and build asset index
        
        Args:
            context: Pipeline context with assets list
        
        Returns:
            Updated context with asset_index
        """
        # Create isolated task directory
        task_dir, task_id = create_task_output_dir()
        context.task_id = task_id
        context.task_dir = Path(task_dir)  # Convert to Path for easier usage
        
        # Determine final video path
        context.final_video_path = get_task_final_video_path(task_id)
        
        logger.info(f"📁 Task directory created: {task_dir}")
        logger.info("🔍 Analyzing uploaded assets...")
        
        assets: List[str] = context.request.get("assets", [])
        if not assets:
            raise ValueError("No assets provided. Please upload at least one image or video.")
        
        total_assets = len(assets)
        logger.info(f"Found {total_assets} assets to analyze")
        
        # Emit initial progress (0-15% for asset analysis)
        self._emit_progress(ProgressEvent(
            event_type="analyzing_assets",
            progress=0.01,
            frame_current=0,
            frame_total=total_assets,
            extra_info="start"
        ))
        
        self.asset_index = {}
        
        for i, asset_path in enumerate(assets, 1):
            asset_path_obj = Path(asset_path)
            
            if not asset_path_obj.exists():
                logger.warning(f"Asset not found: {asset_path}")
                continue
            
            logger.info(f"Analyzing asset {i}/{total_assets}: {asset_path_obj.name}")
            
            # Emit progress for this asset
            progress = 0.01 + (i - 1) / total_assets * 0.14  # 1% - 15%
            self._emit_progress(ProgressEvent(
                event_type="analyzing_asset",
                progress=progress,
                frame_current=i,
                frame_total=total_assets,
                extra_info=asset_path_obj.name
            ))
            
            # Determine asset type
            asset_type = self._get_asset_type(asset_path_obj)
            
            if asset_type == "image":
                analysis_source = context.request.get("source", "runninghub")
                if analysis_source == "api":
                    description = await self.core.api_asset_analysis.analyze_image(asset_path)
                else:
                    # Analyze image using ImageAnalysisService
                    description = await self.core.image_analysis(asset_path, source=analysis_source)
                
                self.asset_index[asset_path] = {
                    "path": asset_path,
                    "type": "image",
                    "name": asset_path_obj.name,
                    "description": description
                }
                
                logger.info(f"✅ Image analyzed: {description[:50]}...")
            
            elif asset_type == "video":
                analysis_source = context.request.get("source", "runninghub")
                try:
                    if analysis_source == "api":
                        description = await self.core.api_asset_analysis.analyze_video(asset_path)
                    else:
                        # Analyze video using VideoAnalysisService
                        description = await self.core.video_analysis(asset_path, source=analysis_source)
                    
                    self.asset_index[asset_path] = {
                        "path": asset_path,
                        "type": "video",
                        "name": asset_path_obj.name,
                        "description": description
                    }
                    
                    logger.info(f"✅ Video analyzed: {description[:50]}...")
                except Exception as e:
                    logger.warning(f"Video analysis failed for {asset_path_obj.name}: {e}, using fallback")
                    self.asset_index[asset_path] = {
                        "path": asset_path,
                        "type": "video",
                        "name": asset_path_obj.name,
                        "description": "Video asset (analysis failed)"
                    }
            
            else:
                logger.warning(f"Unknown asset type: {asset_path}")
        
        logger.success(f"✅ Asset analysis complete: {len(self.asset_index)} assets indexed")
        
        # Store asset index in context
        context.asset_index = self.asset_index
        
        # Emit completion of asset analysis
        self._emit_progress(ProgressEvent(
            event_type="analyzing_assets",
            progress=0.15,
            frame_current=total_assets,
            frame_total=total_assets,
            extra_info="complete"
        ))
        
        return context
    
    async def determine_title(self, context: PipelineContext) -> PipelineContext:
        """
        Use user-provided title if available, otherwise leave empty
        
        Args:
            context: Pipeline context
        
        Returns:
            Updated context with title (may be empty)
        """
        title = context.request.get("video_title")
        
        if title:
            context.title = title
            logger.info(f"📝 Video title: {title} (user-specified)")
        else:
            context.title = ""
            logger.info(f"📝 No video title specified (will be hidden in template)")
        
        return context
    
    async def generate_content(self, context: PipelineContext) -> PipelineContext:
        """
        Generate video script using LLM with structured output
        
        LLM directly assigns assets to scenes - no complex matching logic needed.
        
        Args:
            context: Pipeline context
        
        Returns:
            Updated context with generated script (scenes already have asset_path assigned)
        """
        from pixelle_video.prompts.asset_script_generation import build_asset_script_prompt
        
        logger.info("🤖 Generating video script with LLM...")
        
        # Emit progress for script generation (15% - 25%)
        self._emit_progress(ProgressEvent(
            event_type="generating_script",
            progress=0.16
        ))
        
        # Build prompt for LLM
        intent = context.request.get("intent", context.input_text)
        duration = context.request.get("duration", 30)
        title = context.title  # May be empty if user didn't provide one
        
        # Build deterministic scene plan:
        # - random asset order
        # - image scenes <= 5 seconds
        # - videos use full original duration
        # - no repeat until every asset has been used once
        scene_plan = self._build_asset_scene_plan(target_duration=float(duration))
        context.scene_plan = scene_plan

        visual_duration = sum(float(item.get("duration") or 0) for item in scene_plan)
        target_narration_chars = self._estimate_narration_chars(
            visual_duration=visual_duration,
            tts_speed=context.params.get("tts_speed", 1.0),
        )
        context.planned_total_duration = visual_duration
        context.target_narration_chars = target_narration_chars

        asset_info = []
        for item in scene_plan:
            asset_info.append(
                f"- Planned Scene {item['scene_number']}\n"
                f"  Path: {item['asset_path']}\n"
                f"  Asset type: {item['asset_type']}\n"
                f"  Target duration: {item['duration']:.2f} seconds\n"
                f"  Max narration chars: {item['max_narration_chars']}\n"
                f"  Description: {item['description']}"
            )

        assets_text = "\n".join(asset_info)
        
        # Build prompt using the centralized prompt function
        prompt = build_asset_script_prompt(
            intent=intent,
            duration=duration,
            assets_text=assets_text,
            title=title,
            visual_duration=visual_duration,
            target_narration_chars=target_narration_chars,
        )
        
        # Call LLM with structured output
        script: VideoScript = await self.core.llm(
            prompt=prompt,
            response_type=VideoScript,
            temperature=0.8,
            max_tokens=4000
        )
        
        # Convert to dict format and force it to follow the planned scene schedule.
        raw_script = [scene.model_dump() for scene in script.scenes]
        context.script = self._normalize_script_to_scene_plan(raw_script, scene_plan)

        fallback_narration = " ".join(
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
        
        # Validate asset paths exist
        for scene in context.script:
            asset_path = scene.get("asset_path")
            if asset_path not in self.asset_index:
                # Find closest match (in case LLM slightly modified the path)
                matched = False
                for known_path in self.asset_index.keys():
                    if Path(known_path).name == Path(asset_path).name:
                        scene["asset_path"] = known_path
                        matched = True
                        logger.warning(f"Corrected asset path: {asset_path} -> {known_path}")
                        break
                
                if not matched:
                    # Fallback to first available asset
                    fallback_path = list(self.asset_index.keys())[0]
                    logger.warning(f"Unknown asset path '{asset_path}', using fallback: {fallback_path}")
                    scene["asset_path"] = fallback_path
        
        logger.success(f"✅ Generated script with {len(context.script)} scenes")
        
        # Emit progress after script generation
        self._emit_progress(ProgressEvent(
            event_type="generating_script",
            progress=0.25,
            extra_info="complete"
        ))
        
        # Log script preview
        for scene in context.script:
            narrations = scene.get("narrations", [])
            if isinstance(narrations, str):
                narrations = [narrations]
            narration_preview = " | ".join([n[:30] + "..." if len(n) > 30 else n for n in narrations[:2]])
            asset_name = Path(scene.get("asset_path", "unknown")).name
            logger.info(f"Scene {scene['scene_number']} [{asset_name}]: {narration_preview}")
        
        return context
    
    async def plan_visuals(self, context: PipelineContext) -> PipelineContext:
        """
        Prepare matched scenes from LLM-generated script
        
        Since LLM already assigned asset_path in generate_content, this method
        simply converts the script format to matched_scenes format.
        
        Args:
            context: Pipeline context
        
        Returns:
            Updated context with matched_scenes
        """
        logger.info("🎯 Preparing scene-asset mapping...")
        
        # LLM already assigned asset_path to each scene in generate_content
        # Just convert to matched_scenes format for downstream compatibility
        context.matched_scenes = [
            {
                **scene,
                "matched_asset": scene["asset_path"]  # Alias for compatibility
            }
            for scene in context.script
        ]
        
        # Log asset usage summary
        asset_usage = {}
        for scene in context.matched_scenes:
            asset = scene["matched_asset"]
            asset_usage[asset] = asset_usage.get(asset, 0) + 1
        
        logger.info(f"📊 Asset usage summary:")
        for asset_path, count in asset_usage.items():
            logger.info(f"   {Path(asset_path).name}: {count} scene(s)")
        
        return context
    
    async def initialize_storyboard(self, context: PipelineContext) -> PipelineContext:
        """
        Initialize storyboard from matched scenes
        
        Args:
            context: Pipeline context
        
        Returns:
            Updated context with storyboard
        """
        from pixelle_video.models.storyboard import (
            Storyboard,
            StoryboardFrame, 
            StoryboardConfig
        )
        from datetime import datetime
        
        # Extract all narrations in order for compatibility
        all_narrations = []
        for scene in context.matched_scenes:
            narrations = scene.get("narrations", [scene.get("narration", "")])
            if isinstance(narrations, str):
                narrations = [narrations]
            all_narrations.extend(narrations)
        
        context.narrations = all_narrations
        
        # Get template dimensions
        # Use asset_default.html template which supports both image and video assets
        # (conditionally shows background image or provides transparent overlay)
        template_name = "1080x1920/asset_default.html"
        # Extract dimensions from template name (e.g., "1080x1920")
        try:
            dims = template_name.split("/")[0].split("x")
            media_width = int(dims[0])
            media_height = int(dims[1])
        except:
            # Default to 1080x1920
            media_width = 1080
            media_height = 1920
        
        # Create StoryboardConfig
        context.config = StoryboardConfig(
            task_id=context.task_id,
            n_storyboard=len(context.matched_scenes),  # Number of scenes
            min_narration_words=5,
            max_narration_words=50,
            video_fps=30,
            tts_inference_mode="local",
            voice_id=context.params.get("voice_id", "ja-JP-NanamiNeural"),
            tts_speed=context.params.get("tts_speed", 1.0),
            media_width=media_width,
            media_height=media_height,
            frame_template=template_name,
            template_params=context.params.get("template_params")
        )
        
        # Create Storyboard
        context.storyboard = Storyboard(
            title=context.title,
            config=context.config,
            created_at=datetime.now()
        )
        
        # Create StoryboardFrames - one per scene
        for i, scene in enumerate(context.matched_scenes):
            # Get first narration for the frame (we'll combine audios later)
            narrations = scene.get("narrations", [scene.get("narration", "")])
            if isinstance(narrations, str):
                narrations = [narrations]
            
            # Scene-level narration is intentionally empty.
            # Final subtitles are generated from context.full_narration, not from visual scenes.
            main_narration = ""
            
            frame = StoryboardFrame(
                index=i,
                narration=main_narration,
                image_prompt=None,  # We're using user assets, not generating images
                created_at=datetime.now()
            )
            
            # Get asset path and determine actual media type from asset_index
            asset_path = scene["matched_asset"]
            asset_metadata = self.asset_index.get(asset_path, {})
            asset_type = asset_metadata.get("type", "image")  # Default to image if not found
            
            # Set media type and path based on actual asset type
            if asset_type == "video":
                frame.media_type = "video"
                frame.video_path = asset_path
                logger.debug(f"Scene {i}: Using video asset: {Path(asset_path).name}")
            else:
                frame.media_type = "image"
                frame.image_path = asset_path
                logger.debug(f"Scene {i}: Using image asset: {Path(asset_path).name}")
            
            # Store scene info for later audio generation
            frame._scene_data = scene  # Temporary storage for multi-narration
            frame.target_duration = float(scene.get("duration") or 0)
            
            context.storyboard.frames.append(frame)
        
        logger.info(f"✅ Created storyboard with {len(context.storyboard.frames)} scenes")
        
        return context
    
    async def produce_assets(self, context: PipelineContext) -> PipelineContext:
        """
        Generate scene videos using FrameProcessor (asset + multiple narrations + template)
        
        Args:
            context: Pipeline context
        
        Returns:
            Updated context with processed frames
        """
        logger.info("🎬 Producing scene videos...")
        
        storyboard = context.storyboard
        config = context.config
        total_frames = len(storyboard.frames)
        
        # Progress range: 30% - 85% for frame production
        base_progress = 0.30
        progress_range = 0.55  # 85% - 30%
        
        # Generate one continuous narration audio for the whole video.
        # Use the Edge TTS + VTT path below only. Do not generate a separate plain TTS file here.

        planned_total_duration = float(getattr(context, "planned_total_duration", 0) or 0)
        if planned_total_duration <= 0:
            planned_total_duration = sum(
                float(getattr(frame, "target_duration", 0) or getattr(frame, "duration", 0) or 0)
                for frame in storyboard.frames
            )

        context.full_narration_audio_path = await self._generate_full_narration_audio_with_retries(
            context=context,
            config=config,
            target_duration=planned_total_duration,
        )
        context.full_narration_audio_duration = self._probe_video_duration(context.full_narration_audio_path)
        logger.info(
            f"Full narration audio ready: {context.full_narration_audio_path} "
            f"({context.full_narration_audio_duration:.2f}s)"
        )

        for i, frame in enumerate(storyboard.frames, 1):
            logger.info(f"Producing scene {i}/{total_frames}...")
            
            # Emit progress for this frame (each frame has 4 steps: audio, combine, duration, compose)
            frame_progress = base_progress + (i - 1) / total_frames * progress_range
            self._emit_progress(ProgressEvent(
                event_type="frame_step",
                progress=frame_progress,
                frame_current=i,
                frame_total=total_frames,
                step=1,
                action="audio"
            ))
            
            # Get scene data with narrations
            scene = frame._scene_data
            narrations = scene.get("narrations", [scene.get("narration", "")])
            if isinstance(narrations, str):
                narrations = [narrations]
            
            logger.info(f"Scene {i} has {len(narrations)} narration(s)")
            frame.target_duration = float(scene.get("duration") or 0)
            
            # Step 1: Create silent placeholder audio for this visual scene.
            # The real narration is generated once for the entire video and merged in post-production.
            scene_duration = float(getattr(frame, "target_duration", 0) or scene.get("duration") or 1.0)
            silent_audio_path = Path(context.task_dir) / "frames" / f"{i:02d}_silence.wav"
            self._create_silent_audio(str(silent_audio_path), scene_duration)
            frame.audio_path = str(silent_audio_path)
            frame.duration = scene_duration

            # Step 2: Use FrameProcessor to generate composed frame and video
            # FrameProcessor will handle:
            # - Template rendering (with proper dimensions)
            # - Subtitle composition
            # - Video segment creation
            # - Proper file naming in frames/
            
            # Since we already have the audio and image, we bypass some steps
            # by manually calling the composition steps
            
            # Emit progress for duration calculation
            frame_progress = base_progress + ((i - 1) + 0.5) / total_frames * progress_range
            self._emit_progress(ProgressEvent(
                event_type="frame_step",
                progress=frame_progress,
                frame_current=i,
                frame_total=total_frames,
                step=3,
                action="compose"
            ))
            
            # Get audio duration for frame duration
            import subprocess
            duration_cmd = [
                'ffprobe',
                '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                frame.audio_path
            ]
            duration_result = subprocess.run(duration_cmd, capture_output=True, text=True, check=True)
            frame.duration = float(duration_result.stdout.strip())
            if not getattr(frame, "target_duration", 0):
                frame.target_duration = frame.duration

            api_video_workflow = context.request.get("api_video_workflow")
            api_video_generated_for_frame = False
            if api_video_workflow and frame.media_type == "image" and frame.image_path:
                logger.info(f"Animating scene {i} image via API workflow: {api_video_workflow}")
                api_video_path = get_task_frame_path(context.task_id, frame.index, "video")
                api_video_params = dict(context.request.get("api_video_params") or {})
                api_video_params.pop("use_narration_audio_as_driving_audio", None)
                workflow_info = self._get_api_workflow_info(api_video_workflow)
                adapter_abilities = set((workflow_info or {}).get("adapter_ability_types") or [])

                if "audio_driven_i2v" in adapter_abilities:
                    api_video_params["audio_path"] = frame.audio_path

                # Asset-based scenes should follow narration duration, not the UI default duration.
                api_video_params.pop("duration", None)
                api_duration = max(1, int(math.ceil(getattr(frame, "target_duration", 0) or frame.duration or 5)))

                reference_image_path = frame.image_path
                if getattr(context, "_last_api_video_tail_frame", None):
                    reference_image_path = context._last_api_video_tail_frame
                    logger.info(
                        f"Scene {i}: using previous video tail frame as API first-frame reference: "
                        f"{reference_image_path}"
                    )

                media_result = await self.core.media(
                    prompt=frame.narration or context.input_text or "",
                    workflow=api_video_workflow,
                    media_type="video",
                    image_path=reference_image_path,
                    output_path=api_video_path,
                    duration=api_duration,
                    width=config.media_width,
                    height=config.media_height,
                    **api_video_params,
                )
                frame.media_type = "video"
                frame.video_path = media_result.url
                api_video_generated_for_frame = True
                tail_frame_path = Path(context.task_dir) / "frames" / f"{i:02d}_api_tail_reference.png"
                extracted_tail = self._extract_video_tail_frame(
                    frame.video_path,
                    str(tail_frame_path),
                )
                if extracted_tail:
                    context._last_api_video_tail_frame = extracted_tail
                logger.success(f"✅ API video generated for scene {i}: {frame.video_path}")
            
            # Emit progress for video composition
            frame_progress = base_progress + ((i - 1) + 0.75) / total_frames * progress_range
            self._emit_progress(ProgressEvent(
                event_type="frame_step",
                progress=frame_progress,
                frame_current=i,
                frame_total=total_frames,
                step=4,
                action="video"
            ))
            
            # Use FrameProcessor for proper composition
            processed_frame = await self.core.frame_processor(
                frame=frame,
                storyboard=storyboard,
                config=config,
                total_frames=total_frames
            )

            if api_video_workflow and not api_video_generated_for_frame and processed_frame.video_segment_path:
                tail_frame_path = Path(context.task_dir) / "frames" / f"{i:02d}_tail_reference.png"
                extracted_tail = self._extract_video_tail_frame(
                    processed_frame.video_segment_path,
                    str(tail_frame_path),
                )
                if extracted_tail:
                    context._last_api_video_tail_frame = extracted_tail

            actual_segment_duration = self._probe_video_duration(processed_frame.video_segment_path) if processed_frame.video_segment_path else 0
            if actual_segment_duration > 0:
                processed_frame.duration = actual_segment_duration
            storyboard.total_duration += processed_frame.duration or frame.duration or 0
            
            logger.success(f"✅ Scene {i} complete")
        
        # Emit completion of frame production
        self._emit_progress(ProgressEvent(
            event_type="processing_frame",
            progress=0.85,
            frame_current=total_frames,
            frame_total=total_frames
        ))
        
        return context
    
    async def post_production(self, context: PipelineContext) -> PipelineContext:
        """
        Concatenate scene videos and add BGM
        
        Args:
            context: Pipeline context
        
        Returns:
            Updated context with final video path
        """
        logger.info("🎞️ Concatenating scenes...")
        
        # Emit progress for concatenation (85% - 95%)
        self._emit_progress(ProgressEvent(
            event_type="concatenating",
            progress=0.86
        ))
        
        # Collect video segments from storyboard frames
        scene_videos = [frame.video_segment_path for frame in context.storyboard.frames]
        
        # Generate filename: use title if provided, otherwise use task_id or default name
        if context.title:
            filename = f"{context.title}.mp4"
        else:
            filename = f"{context.task_id}.mp4"  # Use task_id as filename when title is empty
        
        final_video_path = Path(context.task_dir) / filename
        visual_video_path = Path(context.task_dir) / f"visual_{filename}"
        subtitled_video_path = Path(context.task_dir) / f"subtitled_{filename}"
        
        # Get BGM parameters
        bgm_path = context.request.get("bgm_path")
        bgm_volume = context.request.get("bgm_volume", 0.2)
        bgm_mode = context.request.get("bgm_mode", "loop")
        
        if bgm_path:
            logger.info(f"🎵 Adding BGM: {bgm_path} (volume={bgm_volume}, mode={bgm_mode})")
        
        self.core.video.concat_videos(
            videos=scene_videos,
            output=str(visual_video_path),
            method="transition",
            bgm_path=bgm_path,
            bgm_volume=bgm_volume,
            bgm_mode=bgm_mode
        )

        full_narration_audio = getattr(context, "full_narration_audio_path", None)
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
            ass_path = Path(context.task_dir) / "full_narration_subtitles.ass"
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

            self._burn_subtitles(
                video_path=str(narrated_video_path),
                subtitle_path=str(ass_path),
                output_path=str(subtitled_video_path),
            )
            fade_source_video_path = subtitled_video_path
        else:
            fade_source_video_path = visual_video_path

        self._apply_black_fade_in_out(
            video_path=str(fade_source_video_path),
            output_path=str(final_video_path),
            fade_seconds=2.0,
        )
        
        context.final_video_path = str(final_video_path)
        context.storyboard.final_video_path = str(final_video_path)
        context.storyboard.completed_at = datetime.now()

        if not context.storyboard.total_duration:
            context.storyboard.total_duration = self._probe_video_duration(str(final_video_path))
        
        logger.success(f"✅ Final video: {final_video_path}")
        
        # Emit completion of concatenation
        self._emit_progress(ProgressEvent(
            event_type="concatenating",
            progress=0.95,
            extra_info="complete"
        ))
        
        return context
    
    async def finalize(self, context: PipelineContext) -> PipelineContext:
        """
        Finalize and return result
        
        Args:
            context: Pipeline context
        
        Returns:
            Final context
        """
        logger.success(f"🎉 Asset-based video generation complete!")
        logger.info(f"Video: {context.final_video_path}")
        
        # Emit completion
        self._emit_progress(ProgressEvent(
            event_type="completed",
            progress=1.0
        ))
        
        # Persist metadata for history tracking
        await self._persist_task_data(context)
        
        return context
    
    async def _persist_task_data(self, ctx: PipelineContext):
        """
        Persist task metadata and storyboard to filesystem for history tracking
        """
        from pathlib import Path
        
        try:
            storyboard = ctx.storyboard
            task_id = ctx.task_id
            
            if not task_id:
                logger.warning("No task_id in context, skipping persistence")
                return
            
            # Get file size
            video_path_obj = Path(ctx.final_video_path)
            file_size = video_path_obj.stat().st_size if video_path_obj.exists() else 0
            
            # Build metadata
            input_params = {
                "text": ctx.input_text,
                "mode": "asset_based",
                "title": ctx.title or "",
                "n_scenes": len(storyboard.frames) if storyboard else 0,
                "assets": ctx.request.get("assets", []),
                "intent": ctx.request.get("intent"),
                "duration": ctx.request.get("duration"),
                "source": ctx.request.get("source"),
                "voice_id": ctx.request.get("voice_id"),
                "tts_speed": ctx.request.get("tts_speed"),
            }
            
            metadata = {
                "task_id": task_id,
                "created_at": storyboard.created_at.isoformat() if storyboard and storyboard.created_at else None,
                "completed_at": storyboard.completed_at.isoformat() if storyboard and storyboard.completed_at else None,
                "status": "completed",
                
                "input": input_params,
                
                "result": {
                    "video_path": ctx.final_video_path,
                    "duration": storyboard.total_duration if storyboard else 0,
                    "file_size": file_size,
                    "n_frames": len(storyboard.frames) if storyboard else 0
                },
                
                "config": {
                    "llm_model": self.core.config.get("llm", {}).get("model", "unknown"),
                    "llm_base_url": self.core.config.get("llm", {}).get("base_url", "unknown"),
                    "source": ctx.request.get("source", "runninghub"),
                }
            }
            
            # Save metadata
            await self.core.persistence.save_task_metadata(task_id, metadata)
            logger.info(f"💾 Saved task metadata: {task_id}")
            
            # Save storyboard
            if storyboard:
                await self.core.persistence.save_storyboard(task_id, storyboard)
                logger.info(f"💾 Saved storyboard: {task_id}")
            
        except Exception as e:
            logger.error(f"Failed to persist task data: {e}")
            # Don't raise - persistence failure shouldn't break video generation
    
    def _build_asset_scene_plan(self, target_duration: float) -> List[Dict[str, Any]]:
        """
        Build a strict random scene plan.

        Rules:
        - Random asset order.
        - Images are capped at 5 seconds.
        - Videos use their full duration.
        - Assets are not repeated until all available assets have been used once.
        - If target duration is still not reached, randomly reuse assets.
        """
        target_duration = max(float(target_duration or 1), 1.0)

        assets = []
        for asset_path, metadata in self.asset_index.items():
            asset_type = metadata.get("type") or self._get_asset_type(Path(asset_path))
            assets.append({
                "asset_path": asset_path,
                "asset_type": asset_type,
                "description": metadata.get("description", ""),
            })

        if not assets:
            raise ValueError("No valid assets available for scene planning.")

        scene_plan: List[Dict[str, Any]] = []
        total = 0.0
        pass_index = 0

        # Safety guard prevents accidental infinite loops.
        while total < target_duration - 0.25 and pass_index < 20:
            pool = assets.copy()
            random.shuffle(pool)

            added_this_pass = False

            for asset in pool:
                if total >= target_duration - 0.25:
                    break

                remaining = target_duration - total
                asset_path = asset["asset_path"]
                asset_type = asset["asset_type"]

                if asset_type == "video":
                    duration = self._probe_video_duration(asset_path)
                    if duration <= 0:
                        duration = min(5.0, max(1.2, remaining))
                else:
                    # Image scenes must not exceed 5 seconds.
                    if remaining < 0.8 and scene_plan:
                        previous = scene_plan[-1]
                        if previous["asset_type"] == "image" and previous["duration"] + remaining <= 5.0:
                            previous["duration"] = round(previous["duration"] + remaining, 2)
                            previous["max_narration_chars"] = self._narration_char_limit(previous["duration"])
                            total += remaining
                        break

                    duration = min(5.0, remaining)
                    duration = max(1.2, duration)

                scene_plan.append({
                    "scene_number": len(scene_plan) + 1,
                    "asset_path": asset_path,
                    "asset_type": asset_type,
                    "duration": round(float(duration), 2),
                    "max_narration_chars": self._narration_char_limit(float(duration)),
                    "description": asset.get("description", ""),
                })

                total += float(duration)
                added_this_pass = True

            if not added_this_pass:
                break

            # Only after one complete pass can assets be reused.
            pass_index += 1

        logger.info(
            f"Built asset scene plan: scenes={len(scene_plan)}, "
            f"planned_duration={sum(item['duration'] for item in scene_plan):.2f}s, "
            f"target={target_duration:.2f}s"
        )

        return scene_plan

    def _narration_char_limit(self, duration: float) -> int:
        """Approximate Japanese narration length limit for the target seconds."""
        return max(8, min(32, int(float(duration) * 5.5)))



    def _core_theme_keywords(self) -> tuple[str, ...]:
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
- soft spoken Japanese, not stiff written Japanese
- short natural sentences with gentle pauses
- sounds like a real woman speaking quietly, not a narrator reading an advertisement

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

    def _normalize_script_to_scene_plan(
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

    def _compact_narration(self, text: str, limit: int, is_last: bool = False) -> str:
        """
        Deprecated scene-level narration compactor.

        Final subtitles now come from full_narration, not scene narrations.
        This method is kept only for backward compatibility.
        """
        import re

        text = (text or "").strip()
        text = re.sub(r"\s+", "", text)

        if not text:
            return ""

        if len(text) <= limit:
            return text

        return text[:limit].rstrip("\u3001\u3002\uff01\uff1f!?") + "\u3002"


    def _split_full_narration_for_subtitles(self, text: str, max_chars: int = 22) -> List[str]:
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

    def _format_srt_time(self, seconds: float) -> str:
        seconds = max(float(seconds or 0), 0.0)
        millis = int(round((seconds - int(seconds)) * 1000))
        total_seconds = int(seconds)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


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

    def _parse_vtt_time(self, value: str) -> float:
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
        font_size = max(62, min(86, int(width / 14)))

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


    def _write_srt_from_full_narration(self, text: str, total_duration: float, output_path: str) -> str:
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

    def _escape_subtitle_filter_path(self, path: str) -> str:
        value = Path(path).resolve().as_posix()
        value = value.replace(":", r"\:")
        value = value.replace("'", r"\\'")
        return value

    def _apply_black_fade_in_out(self, video_path: str, output_path: str, fade_seconds: float = 2.0) -> str:
        """
        Apply black fade-in at the beginning and black fade-out at the end.
        Video only: audio is copied without changing speed or duration.
        """
        import subprocess

        if not Path(video_path).exists():
            raise RuntimeError(f"Fade source video does not exist: {video_path}")

        duration = self._probe_video_duration(video_path)
        if duration <= 0:
            raise RuntimeError(f"Cannot apply fade because video duration is invalid: {video_path}")

        fade_seconds = max(0.1, min(float(fade_seconds or 2.0), duration / 2.0))
        fade_out_start = max(duration - fade_seconds, 0.0)

        vf = (
            f"fade=t=in:st=0:d={fade_seconds:.3f},"
            f"fade=t=out:st={fade_out_start:.3f}:d={fade_seconds:.3f}"
        )

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

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
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            "-y",
            output_path,
        ]

        logger.info(
            f"Applying black fade in/out: input={video_path}, output={output_path}, "
            f"duration={duration:.2f}s, fade={fade_seconds:.2f}s"
        )

        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            if not Path(output_path).exists() or Path(output_path).stat().st_size <= 0:
                raise RuntimeError(f"Fade output is empty: {output_path}")
            return output_path
        except subprocess.CalledProcessError as exc:
            error = exc.stderr or str(exc)
            logger.error(f"Black fade in/out failed: {error}")
            raise RuntimeError(f"Black fade in/out failed. FFmpeg error: {error}")


    def _burn_subtitles(self, video_path: str, subtitle_path: str = None, output_path: str = None, srt_path: str = None) -> str:
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
        except subprocess.CalledProcessError as exc:
            logger.warning(f"Subtitle burn failed, fallback to no subtitles: {exc.stderr}")
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(video_path, output_path)
            return output_path
        except subprocess.CalledProcessError as exc:
            logger.warning(f"Subtitle burn failed, fallback to no subtitles: {exc.stderr}")
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy(video_path, output_path)
            return output_path


    # Helper methods
    
    def _get_asset_type(self, path: Path) -> str:
        """Determine asset type from file extension"""
        image_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
        video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
        
        ext = path.suffix.lower()
        
        if ext in image_exts:
            return "image"
        elif ext in video_exts:
            return "video"
        else:
            return "unknown"

    def _get_api_workflow_info(self, workflow_key: str) -> dict:
        """Find API workflow metadata for capability-aware adapter behavior."""
        try:
            for workflow in self.core.api_media.list_workflows():
                if workflow.get("key") == workflow_key:
                    return workflow
        except Exception as exc:
            logger.warning(f"Failed to read API workflow metadata for {workflow_key}: {exc}")
        return {}

    def _extract_video_tail_frame(self, video_path: str, output_path: str) -> Optional[str]:
        """Extract the last visible frame from a generated scene video."""
        try:
            import subprocess

            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            extract_cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-sseof",
                "-0.15",
                "-i",
                video_path,
                "-frames:v",
                "1",
                "-q:v",
                "2",
                "-y",
                output_path,
            ]
            subprocess.run(extract_cmd, capture_output=True, text=True, check=True)
            if Path(output_path).exists():
                logger.info(f"Extracted tail reference frame: {output_path}")
                return output_path
        except Exception as exc:
            logger.debug(f"Primary tail-frame extraction failed for {video_path}: {exc}")

        try:
            import subprocess

            probe_cmd = [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                video_path,
            ]
            probe = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
            duration = float(probe.stdout.strip() or 0)
            seek_time = max(duration - 0.5, 0)
            fallback_cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                video_path,
                "-ss",
                f"{seek_time:.3f}",
                "-frames:v",
                "1",
                "-q:v",
                "2",
                "-y",
                output_path,
            ]
            subprocess.run(fallback_cmd, capture_output=True, text=True, check=True)
            if Path(output_path).exists():
                logger.info(f"Extracted tail reference frame with fallback: {output_path}")
                return output_path
        except Exception as exc:
            logger.warning(f"Failed to extract tail frame from {video_path}: {exc}")
        return None


    def _create_silent_audio(self, output_path: str, duration: float) -> str:
        """Create silent audio for a visual segment."""
        try:
            import subprocess

            duration = max(float(duration or 1.0), 0.5)
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)

            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-t",
                f"{duration:.3f}",
                "-y",
                output_path,
            ]
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            return output_path
        except Exception as exc:
            logger.error(f"Failed to create silent audio: {exc}")
            raise

    def _fit_audio_to_duration(self, input_audio: str, target_duration: float, output_audio: str) -> str:
        """
        Deprecated. Do not time-stretch narration audio.
        Duration must be controlled by narration text length, not by audio filters.
        """
        return input_audio
    def _probe_video_duration(self, video_path: str) -> float:
        """Return video duration using ffprobe, or 0 on failure."""
        try:
            import subprocess

            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    video_path,
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            return float(result.stdout.strip() or 0)
        except Exception as exc:
            logger.warning(f"Failed to probe video duration for {video_path}: {exc}")
            return 0.0
    
