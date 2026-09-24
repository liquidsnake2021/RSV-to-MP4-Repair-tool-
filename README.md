# Sony RSV → MP4 Repair Tool

A free, simple tool to repair unfinalized Sony `.RSV` video files into playable `.MP4` files.

**No donor file needed.** Just select your RSV file and click Repair.

## What are RSV files?

Sony cameras (A7S III, FX3, A7 IV, etc.) create `.RSV` files when a recording is interrupted unexpectedly — battery dies, power loss, or card ejection. The video data is intact but the file is missing its MP4 container metadata, making it unplayable.

## How to Use

1. Double-click **`RSV Repair.vbs`** to launch the GUI
2. Click **Browse** and select your `.RSV` file
3. Optionally choose an **Output Folder** (defaults to same folder as the RSV)
4. Click **Repair**
5. Wait for the repair to complete

You can also drag & drop `.RSV` files onto the window (requires the `windnd` Python package).

## Requirements

- **Python 3.8+** with `pythonw` on PATH
- Optional: `pip install windnd` for drag & drop support

No FFmpeg required.

## How It Works

Sony RSV files contain the raw MP4 media data (video, audio, and timed metadata) but are missing the MP4 container structure (ftyp + moov boxes). This tool:

1. **Parses the interleaved chunk structure** — detects metadata, video, and audio boundaries
2. **Extracts codec configuration** — reads the camera's real SPS/PPS from Sony's embedded `kkad` metadata box
3. **Builds a valid MP4 container** — constructs ftyp, moov (with proper sample tables), and writes the mdat

Your original `.RSV` file is **never modified**. The repaired file is saved as `filename_repaired.mp4`.

## Supported Formats

- Sony XAVC-S recordings (H.264 High 4:2:2 / High 4:2:0)
- Sony XAVC-HS recordings (HEVC / H.265 Main 10)
- 4K (3840×2160) and 1080p resolutions
- PCM audio (48kHz, 16-bit stereo)
- Files of any size (tested up to 51 GB)

## Limitations

- Designed for Sony cameras that produce RSV files (A7S III, FX3, A7 IV, etc.)
- May not handle files with physically damaged SD card sectors
- Extremely short recordings (< 1 second) may not have enough data for detection

## Files

| File | Description |
|------|-------------|
| `RSV Repair.vbs` | Double-click launcher (no console window) |
| `rsv_repair.py` | GUI application |
| `build_moov.py` | Repair engine |
