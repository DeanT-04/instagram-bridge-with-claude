"""Media pipeline: allow-listed CDN downloads, keyframes and transcripts.

Submodules:

* :mod:`heliograph.media.download` - HTTPS, host allow-listed, size-capped atomic download.
* :mod:`heliograph.media.frames` - scene/interval keyframes, perceptual dedupe, contact sheet.
* :mod:`heliograph.media.transcribe` - faster-whisper transcription with timestamps.
* :mod:`heliograph.media.ffmpeg` - safe ffmpeg/ffprobe subprocess helpers.

Import the submodules directly; this package does not eagerly import the heavy ones.
"""
