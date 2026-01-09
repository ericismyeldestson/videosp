"""
Video Subtitle Generator - Cloud Backend
Handles video rendering with FFmpeg on Render.com
"""

import os
import uuid
import subprocess
import tempfile
import shutil
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
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


@app.get("/")
async def root():
    return {"status": "ok", "service": "Video Subtitle Generator API"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/render")
async def render_video(
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
        
        # Save SRT file
        srt_path = job_dir / "subtitles.srt"
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(srt_content)
        
        # Output path
        output_path = job_dir / "output.mp4"
        
        # Build force_style string
        force_style = (
            f"FontSize={font_size},"
            f"MarginV={margin_v},"
            f"FontName={font_name},"
            f"PrimaryColour=&H00FFFFFF,"
            f"OutlineColour=&H00000000,"
            f"Outline=2,"
            f"Bold=1"
        )
        
        # Escape paths for FFmpeg
        srt_escaped = str(srt_path).replace("\\", "/").replace(":", "\\:")
        
        # FFmpeg command
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
            str(output_path)
        ]
        
        print(f"[FFmpeg] Running: {' '.join(cmd)}")
        
        # Run FFmpeg
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600  # 10 minutes timeout
        )
        
        if result.returncode != 0:
            print(f"[FFmpeg] Error: {result.stderr}")
            raise HTTPException(status_code=500, detail=f"FFmpeg error: {result.stderr[-500:]}")
        
        if not output_path.exists():
            raise HTTPException(status_code=500, detail="Output file not created")
        
        # Return the processed video
        return FileResponse(
            str(output_path),
            media_type="video/mp4",
            filename=f"subtitled_{job_id}.mp4",
            background=None  # Will clean up after
        )
        
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Processing timeout")
    except Exception as e:
        print(f"[Error] {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Cleanup will happen after response is sent
        pass


@app.on_event("startup")
async def startup():
    print("[INFO] Video Subtitle Generator Cloud API Starting...")
    # Check FFmpeg
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
        print(f"[INFO] FFmpeg available: {result.stdout.split(chr(10))[0]}")
    except Exception as e:
        print(f"[WARNING] FFmpeg not found: {e}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
