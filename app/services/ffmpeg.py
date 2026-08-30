from __future__ import annotations

import asyncio
import os
import shutil
import subprocess

from app.config import settings


async def extract_last_frame(video_path: str, output_path: str) -> str:
    """Extract the last frame of a video as a PNG image."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-sseof", "-0.1",
        "-i", video_path,
        "-frames:v", "1",
        "-update", "1",
        output_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"extract_last_frame failed: {stderr.decode()}")
    return output_path


async def normalize_clip(input_path: str, output_path: str) -> str:
    """Normalize a clip to consistent resolution, fps, and codec."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-r", str(settings.output_fps),
        "-s", f"{settings.output_width}x{settings.output_height}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-an", output_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"normalize_clip failed: {stderr.decode()}")
    return output_path


async def get_video_duration(path: str) -> float:
    """Get duration of a video in seconds."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return float(stdout.decode().strip())


async def stitch_clips(
    clip_paths: list[str],
    chain_flags: list[bool],
    output_path: str,
    work_dir: str,
) -> str:
    """
    Concatenate clips with crossfade between non-chained scenes.
    Chained clips (last-frame continuation) get hard cuts.

    `work_dir` holds the normalized clips and the concat list. The caller picks
    it and it must be unique per job — same rule as `out_path` for providers.
    It is scratch, and this function removes it on the way out.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    if len(clip_paths) == 1:
        cmd = ["ffmpeg", "-y", "-i", clip_paths[0], "-c", "copy", output_path]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"stitch single clip failed: {stderr.decode()}")
        return output_path

    # Normalize all clips first
    norm_dir = os.path.join(work_dir, "normalized")
    os.makedirs(norm_dir, exist_ok=True)

    norm_paths = []
    for i, cp in enumerate(clip_paths):
        norm_path = os.path.join(norm_dir, f"norm_{i}.mp4")
        await normalize_clip(cp, norm_path)
        norm_paths.append(norm_path)

    # Get durations for crossfade offsets
    durations = []
    for p in norm_paths:
        durations.append(await get_video_duration(p))

    # Build complex filter with xfade
    xfade_dur = settings.crossfade_duration
    inputs = []
    for p in norm_paths:
        inputs.extend(["-i", p])

    # Build xfade filter chain
    n = len(norm_paths)
    if n == 2:
        # Simple case: just one transition
        use_xfade = not chain_flags[1]
        if use_xfade:
            offset = durations[0] - xfade_dur
            filter_str = f"[0:v][1:v]xfade=transition=fade:duration={xfade_dur}:offset={offset}[outv]"
        else:
            # Hard cut via concat
            filter_str = f"[0:v][1:v]concat=n=2:v=1:a=0[outv]"

        cmd = ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", filter_str,
            "-map", "[outv]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-r", str(settings.output_fps),
            "-an", output_path,
        ]
    else:
        # Multi-clip: use concat for simplicity in POC
        # Create a concat file
        concat_file = os.path.join(work_dir, "concat.txt")
        with open(concat_file, "w") as f:
            for p in norm_paths:
                f.write(f"file '{p}'\n")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", concat_file,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-r", str(settings.output_fps),
            "-an", output_path,
        ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"stitch failed: {stderr.decode()}")
    finally:
        # Also on failure: stderr is already in the exception and a retry
        # re-normalizes from the clips anyway.
        shutil.rmtree(work_dir, ignore_errors=True)

    return output_path
