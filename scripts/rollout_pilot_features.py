"""Advance the pilot feature flags in the approved rollout order.

Usage:
  python scripts/rollout_pilot_features.py --stage 1
  ... repeat smoke verification, then stage 2 through 5.

Stages: 1 conversations; 2 document viewer/PDF; 3 FAQ; 4 support chat;
5 proposals/crawler/OCR. Automatic approval/import are always disabled.
"""
from __future__ import annotations

import argparse
import json
import sys
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from api.pilot_feature_flags import FEATURE_ORDER, save_flags

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--stage", type=int, required=True, choices=range(0, len(FEATURE_ORDER) + 1))
args = parser.parse_args()
payload = save_flags(stage=args.stage, updated_by="pilot-rollout-script")
print(json.dumps(payload, ensure_ascii=True, indent=2))
