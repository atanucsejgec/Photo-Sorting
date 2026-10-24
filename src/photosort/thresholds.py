"""Tunable limits for the duplicate and repeated-shot finders, in one place.

All values are the builder's defaults (design section 9) and can be tuned.
"""

# Duplicates: resized or recompressed copies of one photo.
DUP_HASH_MAX = 6  # max perceptual-hash Hamming distance (of 64 bits)
DUP_ASPECT_TOL = 0.02  # max relative difference in width/height ratio
SAME_MOMENT_S = 2  # max seconds between two copies' taken times

# Repeated shots: near-identical photos taken in quick succession.
REPEAT_WINDOW_S = 3  # max seconds between neighbouring shots
REPEAT_HASH_MAX = 12  # max perceptual-hash Hamming distance (of 64 bits)

# Sharpness, relative to the sharpest photo in a group.
SHARP_SLIGHTLY = 0.85  # below this fraction: "slightly blurry"
SHARP_BLURRY = 0.6  # below this fraction: "blurry"
SHARPNESS_WIDTH = 512  # images are resized to this width before measuring

THUMB_PX = 240  # longest side of review thumbnails
