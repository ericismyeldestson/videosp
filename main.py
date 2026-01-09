"""
Video Subtitle Generator - Cloud Backend
Handles video rendering with FFmpeg on Render.com
"""

import os
import uuid
import subprocess
import tempfile
import shutil
import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="Video Subtitle Generator API")

# CORS for web frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Temp directory for processing
TEMP_DIR = Path(tempfile.gettempdir()) / "video_subtitle"
TEMP_DIR.mkdir(exist_ok=True)


class SubtitleStyle(BaseModel):
    font_size: int = 24
    margin_v: int = 30
    font_name: str = "Arial"


def cleanup_job_dir(job_dir: Path):
    """Clean up job directory after response is sent."""
    try:
        if job_dir.exists():
            shutil.rmtree(job_dir)
            print(f"[Cleanup] Deleted job directory: {job_dir}")
    except Exception as e:
        print(f"[Cleanup] Error deleting {job_dir}: {e}")


def get_storage_usage():
    """Get current storage usage in MB."""
    total_size = 0
    if TEMP_DIR.exists():
        for f in TEMP_DIR.rglob("*"):
            if f.is_file():
                total_size += f.stat().st_size
    return total_size / (1024 * 1024)  # Convert to MB


@app.get("/")
async def root():
    storage_mb = get_storage_usage()
    return {
        "status": "ok", 
        "service": "Video Subtitle Generator API",
        "temp_storage_mb": round(storage_mb, 2)
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/storage")
async def storage_info():
    """Get storage usage information."""
    storage_mb = get_storage_usage()
    job_count = len(list(TEMP_DIR.iterdir())) if TEMP_DIR.exists() else 0
    return {
        "temp_storage_mb": round(storage_mb, 2),
        "job_directories": job_count,
        "temp_dir": str(TEMP_DIR)
    }


@app.delete("/cleanup")
async def manual_cleanup():
    """Manually clean up all temporary files."""
    try:
        if TEMP_DIR.exists():
            before_mb = get_storage_usage()
            shutil.rmtree(TEMP_DIR)
            TEMP_DIR.mkdir(exist_ok=True)
            return {
                "status": "cleaned",
                "freed_mb": round(before_mb, 2)
            }
        return {"status": "already_empty"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/render")
async def render_video(
    background_tasks: BackgroundTasks,
    video: UploadFile = File(...),
    srt_content: str = Form(...),
    font_size: int = Form(24),
    margin_v: int = Form(30),
    font_name: str = Form("Arial")
):
    """
    Render video with burned-in subtitles.
    Accepts video file upload and SRT content.
    Returns processed video file.
    Files are automatically cleaned up after response.
    """
    job_id = str(uuid.uuid4())[:8]
    job_dir = TEMP_DIR / job_id
    job_dir.mkdir(exist_ok=True)
    
    try:
        # Save uploaded video
        video_path = job_dir / "input.mp4"
        with open(video_path, "wb") as f:
            content = await video.read()
            f.write(content)
        
        print(f"[Job {job_id}] Received video: {len(content) / (1024*1024):.2f} MB")
        
        # Save SRT file
        srt_path = job_dir / "subtitles.srt"
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(srt_content)
        
        # Output path
        output_path = job_dir / "output.mp4"
        
        # Build force_style string - use Noto Sans CJK for Chinese support
        actual_font = "Noto Sans CJK SC" if "Source Han" in font_name or "Noto" in font_name else font_name
        
        force_style = (
            f"FontSize={font_size},"
            f"MarginV={margin_v},"
            f"FontName={actual_font},"
            f"PrimaryColour=&H00FFFFFF,"
            f"OutlineColour=&H00000000,"
            f"Outline=2,"
            f"Bold=1"
        )
        
        # Escape paths for FFmpeg
        srt_escaped = str(srt_path).replace("\\", "/").replace(":", "\\:")
        
        # FFmpeg command - strip metadata to prevent filename overlay
        cmd = [
            "ffmpeg",
            "-y",
            "-i", str(video_path),
            "-vf", f"subtitles='{srt_escaped}':force_style='{force_style}'",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-map_metadata", "-1",  # Strip all metadata
            "-metadata", "title=",  # Clear title
            str(output_path)
        ]
        
        print(f"[Job {job_id}] Running FFmpeg...")
        
        # Run FFmpeg
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600  # 10 minutes timeout
        )
        
        if result.returncode != 0:
            print(f"[Job {job_id}] FFmpeg Error: {result.stderr}")
            # Clean up on error
            cleanup_job_dir(job_dir)
            raise HTTPException(status_code=500, detail=f"FFmpeg error: {result.stderr[-500:]}")
        
        if not output_path.exists():
            cleanup_job_dir(job_dir)
            raise HTTPException(status_code=500, detail="Output file not created")
        
        output_size = output_path.stat().st_size / (1024*1024)
        print(f"[Job {job_id}] Output ready: {output_size:.2f} MB")
        
        # Schedule cleanup after response is sent
        background_tasks.add_task(cleanup_job_dir, job_dir)
        
        # Return the processed video
        return FileResponse(
            str(output_path),
            media_type="video/mp4",
            filename=f"subtitled_{job_id}.mp4"
        )
        
    except subprocess.TimeoutExpired:
        cleanup_job_dir(job_dir)
        raise HTTPException(status_code=500, detail="Processing timeout")
    except HTTPException:
        raise
    except Exception as e:
        print(f"[Job {job_id}] Error: {e}")
        cleanup_job_dir(job_dir)
        raise HTTPException(status_code=500, detail=str(e))


@app.on_event("startup")
async def startup():
    print("[INFO] Video Subtitle Generator Cloud API Starting...")
    # Check FFmpeg
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
        print(f"[INFO] FFmpeg available: {result.stdout.split(chr(10))[0]}")
    except Exception as e:
        print(f"[WARNING] FFmpeg not found: {e}")
    
    # Report storage usage
    storage_mb = get_storage_usage()
    print(f"[INFO] Current temp storage: {storage_mb:.2f} MB")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
