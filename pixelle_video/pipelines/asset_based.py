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
            title=title
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

        planned_total_duration = sum(float(item.get("duration") or 0) for item in scene_plan)
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
            tts_speed=context.params.get("tts_speed", 1.2),
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
            
            # Use first narration as the main text (for subtitle)
            # We'll combine all narrations in the audio
            main_narration = " ".join(narrations)  # Combine for subtitle display
            
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
        full_narration_text = getattr(context, "full_narration", "") or context.input_text or ""
        full_audio_path = Path(context.task_dir) / "frames" / "00_full_narration.mp3"
        full_audio_path.parent.mkdir(parents=True, exist_ok=True)

        await self.core.tts(
            text=full_narration_text,
            output_path=str(full_audio_path),
            voice=config.voice_id,
            speed=config.tts_speed
        )

        planned_total_duration = float(getattr(context, "planned_total_duration", 0) or 0)
        if planned_total_duration <= 0:
            planned_total_duration = sum(
                float(getattr(frame, "target_duration", 0) or getattr(frame, "duration", 0) or 0)
                for frame in storyboard.frames
            )

        fitted_full_audio_path = Path(context.task_dir) / "frames" / "00_full_narration_fitted.mp3"
        context.full_narration_audio_path = self._fit_audio_to_duration(
            str(full_audio_path),
            planned_total_duration,
            str(fitted_full_audio_path),
        )
        logger.info(f"? Full narration audio ready: {context.full_narration_audio_path}")

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
            visual_duration = self._probe_video_duration(str(visual_video_path))
            fitted_audio_path = Path(context.task_dir) / "frames" / "00_full_narration_final_fit.mp3"
            final_audio = self._fit_audio_to_duration(
                full_narration_audio,
                visual_duration,
                str(fitted_audio_path),
            )

            self.core.video.merge_audio_video(
                video=str(visual_video_path),
                audio=final_audio,
                output=str(final_video_path),
                replace_audio=False,
                audio_volume=1.0,
                video_volume=1.0,
                auto_adjust_duration=False,
            )
        else:
            final_video_path = visual_video_path
        
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



    def _build_partner_full_narration(self, intent: str, llm_text: str, target_duration: float) -> str:
        """
        Build one continuous narration that must clearly express the partner-seeking theme.
        The LLM text can be used only when it already contains the key theme.
        """
        import re

        target_duration = max(float(target_duration or 1), 1.0)
        char_limit = max(22, int(target_duration * 5.8))

        candidate = re.sub(r"\s+", "", (llm_text or "").strip())

        required_keywords = ("38?", "40?", "??", "??")
        has_theme = all(keyword in candidate for keyword in required_keywords)

        # For this project, theme clarity is more important than preserving generic LLM wording.
        if target_duration <= 8:
            text = "38???????40??????????????????????????"
        elif target_duration <= 12:
            text = "38???????40?????????????????????????????????????????????????"
        elif target_duration <= 18:
            text = "??38??????????????????????????????????????40??????????????????????????????????"
        elif target_duration <= 25:
            text = "??38?????????????????????????????????????????????????40?????????????????????????????????????????????????????????"
        else:
            text = "??38????????????????????????????????????????????????????????????????40?????????????????????????????????????????????????????????????????????????"

        # If LLM already strongly includes the theme and fits, allow it.
        if has_theme and 20 <= len(candidate) <= char_limit:
            text = candidate

        if len(text) <= char_limit:
            return text

        sentences = re.split(r"(?<=[???!?])", text)
        result = ""
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(result + sentence) <= char_limit:
                result += sentence

        if result:
            return result

        return text[:char_limit].rstrip("????!?") + "?"


    def _compact_full_narration(self, text: str, target_duration: float) -> str:
        """
        Keep one continuous Japanese narration roughly within target duration.
        """
        import re

        target_duration = max(float(target_duration or 1), 1.0)

        # Japanese narration rough estimate:
        # 5.5 - 6 chars per second is usually safer for gentle female narration.
        char_limit = max(18, int(target_duration * 5.8))

        text = (text or "").strip()
        text = re.sub(r"\s+", "", text)

        if not text:
            text = "?????????????????????????????????????"

        if len(text) <= char_limit:
            return text

        sentences = re.split(r"(?<=[???!?])", text)
        result = ""

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(result + sentence) <= char_limit:
                result += sentence

        if result:
            return result

        return text[:char_limit].rstrip("????!?") + "?"


    def _normalize_script_to_scene_plan(
        self,
        raw_script: List[Dict[str, Any]],
        scene_plan: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Force LLM output to match the planned scene count, order, assets and durations."""
        normalized: List[Dict[str, Any]] = []

        for idx, planned in enumerate(scene_plan):
            raw_scene = raw_script[idx] if idx < len(raw_script) else {}
            narrations = raw_scene.get("narrations") or raw_scene.get("narration") or []

            if isinstance(narrations, str):
                narrations = [narrations]

            narration_text = " ".join(str(item).strip() for item in narrations if str(item).strip())
            narration_text = self._compact_narration(
                narration_text,
                planned["max_narration_chars"],
                is_last=(idx == len(scene_plan) - 1),
            )

            normalized.append({
                "scene_number": planned["scene_number"],
                "asset_path": planned["asset_path"],
                "narrations": [narration_text],
                "duration": planned["duration"],
            })

        return normalized

    def _compact_narration(self, text: str, limit: int, is_last: bool = False) -> str:
        """Keep narration short enough for the planned scene duration."""
        import re

        text = (text or "").strip()
        text = re.sub(r"\s+", "", text)

        if not text:
            text = (
                "?????????????????????????"
                if is_last
                else "???????????????????"
            )

        if len(text) <= limit:
            return text

        parts = re.split(r"(?<=[???!?])", text)
        result = ""

        for part in parts:
            part = part.strip()
            if not part:
                continue
            if len(result + part) <= limit:
                result += part

        if result:
            return result

        return text[:limit].rstrip("????!?") + "?"

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
        """Speed-adjust narration audio so it roughly matches target video duration."""
        try:
            import subprocess

            target_duration = max(float(target_duration or 1.0), 1.0)
            audio_duration = self._probe_video_duration(input_audio)

            if audio_duration <= 0:
                return input_audio

            tempo = audio_duration / target_duration

            # Small differences do not need processing.
            if 0.96 <= tempo <= 1.04:
                return input_audio

            filters = []
            remaining = tempo

            while remaining > 2.0:
                filters.append("atempo=2.0")
                remaining /= 2.0

            while remaining < 0.5:
                filters.append("atempo=0.5")
                remaining /= 0.5

            filters.append(f"atempo={remaining:.6f}")

            Path(output_audio).parent.mkdir(parents=True, exist_ok=True)

            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                input_audio,
                "-filter:a",
                ",".join(filters),
                "-y",
                output_audio,
            ]
            subprocess.run(cmd, capture_output=True, text=True, check=True)

            if Path(output_audio).exists():
                logger.info(
                    f"Fitted narration audio: original={audio_duration:.2f}s, "
                    f"target={target_duration:.2f}s, tempo={tempo:.3f}"
                )
                return output_audio

            return input_audio
        except Exception as exc:
            logger.warning(f"Failed to fit narration audio duration: {exc}")
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
    
