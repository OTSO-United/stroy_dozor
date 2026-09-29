"""Verify the packaged detector without a database or media writes."""

import argparse
import json
import time

import numpy as np

from .vision import OnnxDetector


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--cpu", action="store_true")
    group.add_argument("--require-cuda", action="store_true")
    args = parser.parse_args()
    started = time.perf_counter()
    model = OnnxDetector(providers=["CPUExecutionProvider"] if args.cpu else None)
    if args.require_cuda and "CUDAExecutionProvider" not in model.info["providers"]:
        raise RuntimeError("CUDAExecutionProvider is not active")
    size = model.info["input_size"]
    detections = model.detect(np.zeros((size, size, 3), dtype=np.uint8))
    print(
        json.dumps(
            {
                "status": "ok",
                "model": model.info,
                "blank_frame_detections": len(detections),
                "load_and_inference_ms": round(
                    (time.perf_counter() - started) * 1000, 1
                ),
                "scope": "runtime smoke, not accuracy evaluation",
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
