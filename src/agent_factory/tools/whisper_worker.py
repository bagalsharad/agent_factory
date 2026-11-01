"""Isolated Whisper transcription process (invoked by get_video_transcript).

Runs in its own process so that a long or hung transcription can be killed on timeout and its memory is released.
The parent sets HF_HUB_OFFLINE=1: the model must already be installed (`agent-factory media setup`).
Prints {"language": ..., "segments": [{"start", "end", "text"}]} as JSON on stdout.
"""

from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--language", default=None)
    parser.add_argument("path")
    args = parser.parse_args()
    import mlx_whisper

    result = mlx_whisper.transcribe(args.path, path_or_hf_repo=args.model, language=args.language,
                                    condition_on_previous_text=False, verbose=None)
    segments = [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in result.get("segments", [])]
    json.dump({"language": result.get("language"), "segments": segments}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
