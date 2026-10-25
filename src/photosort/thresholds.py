"""Tunable limits for the finders, folder names and metadata readers, in one place.

All values are the builder's defaults (design section 9) and can be tuned.
"""

# Duplicates: resized or recompressed copies of one photo.
DUP_HASH_MAX = 6  # max perceptual-hash Hamming distance (of 64 bits)
DUP_ASPECT_TOL = 0.02  # max relative difference in width/height ratio
# When both are dated, copies carry the same second; burst shots are a second
# or more apart, so any difference means two shots, not two copies.
SAME_MOMENT_S = 0  # max seconds between two copies' taken times

# Repeated shots: near-identical photos taken in quick succession.
REPEAT_WINDOW_S = 3  # max seconds between neighbouring shots
REPEAT_HASH_MAX = 12  # max perceptual-hash Hamming distance (of 64 bits)

# Sharpness, relative to the sharpest photo in a group.
SHARP_SLIGHTLY = 0.85  # below this fraction: "slightly blurry"
SHARP_BLURRY = 0.6  # below this fraction: "blurry"
SHARPNESS_WIDTH = 512  # images are resized to this width before measuring

THUMB_PX = 240  # longest side of review thumbnails

# Faces: matching a face to a remembered person by embedding similarity.
FACE_SURE = 0.45  # at or above: surely that person
FACE_UNSURE = 0.30  # from here up to FACE_SURE: ask the owner; below: a stranger
# Folder names made from data (people, cameras, places, events): at most this
# many characters and UTF-8 bytes (Linux file systems allow 255 bytes;
# Bengali takes 3 per character).
FOLDER_NAME_MAX = 100
FOLDER_NAME_MAX_BYTES = 200
FACE_FOLDER_MAX = FOLDER_NAME_MAX  # person and together folders
FACE_FOLDER_MAX_BYTES = FOLDER_NAME_MAX_BYTES
PERSON_NAME_MAX = 50  # longest name of a remembered person, in characters
PERSON_NAME_MAX_BYTES = 120  # ... and in UTF-8 bytes
# Face detection (SCRFD).
FACE_DET_SCORE = 0.5  # min detector score for a face
FACE_NMS_IOU = 0.4  # a detection overlapping a better one at or above this IoU is dropped
FACE_DET_SIZE = 640  # the detector's square input, in pixels
FACE_DECODE_PX = 1600  # photos are decoded at most this long side for faces
FACE_MIN_PX = 24  # faces whose shortest box side (decoded pixels) is below this are ignored
FACE_EMBED_BATCH = 32  # faces described in one recognition run (about 11 MB each)
# A face scan whose first this many photos all fail with an error (not just
# an unreadable photo) stops: the face models, not the photos, are at fault.
FACE_ENGINE_FAILS = 5
FACE_CROP_MARGIN = 0.3  # face pictures for the UI add this fraction of the face on each side
PERSON_THUMB_PX = 160  # longest side of a remembered person's face picture

# Video metadata: the most box payload read from one video, in bytes (box
# headers are read by seeking; the video data itself is never read).
MP4_META_MAX = 4 * 1024 * 1024
