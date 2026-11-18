#!/usr/bin/env bash
# Converts what capture-demo.mjs wrote into the files the site serves: each PNG screenshot to a
# WebP at most 1600 px wide in public/assets/demo/, and the raw video to a small VP9 WebM.
#   tools/optimise.sh RAW_DIR      (needs ffmpeg with libwebp and libvpx-vp9)
set -euo pipefail
raw=${1:?usage: tools/optimise.sh RAW_DIR}
out=public/assets/demo
mkdir -p "$out"
for png in "$raw"/*.png; do
  name=$(basename "$png" .png)
  ffmpeg -loglevel error -y -i "$png" -vf "scale='min(1600,iw)':-2:flags=lanczos" \
    -c:v libwebp -quality 82 -compression_level 6 "$out/$name.webp"
done
if [[ -f "$raw/walkthrough-raw.webm" ]]; then
  # A screen recording: few colours, little motion. 10 fps and a high CRF keep it small.
  ffmpeg -loglevel error -y -i "$raw/walkthrough-raw.webm" -an -vf "fps=10,scale=1280:-2" \
    -c:v libvpx-vp9 -b:v 0 -crf 42 -row-mt 1 -deadline good -cpu-used 2 "$out/walkthrough.webm"
fi
du -ch "$out"/* | tail -1
