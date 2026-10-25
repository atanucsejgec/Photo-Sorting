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
# ... and to at most this many rows, so a long thin strip (a 1x10000 banner)
# can't take gigabytes; only shapes taller than 32:1 are squeezed.
SHARPNESS_MAX_ROWS = 32 * SHARPNESS_WIDTH

THUMB_PX = 240  # longest side of review thumbnails
PREVIEW_PX = 1600  # longest side of a photo opened from a review grid

# Bad photos, measured on the greyscale photo SHARPNESS_WIDTH wide (grey
# values 0-255); the first rule that matches gives the reason.
BAD_FLAT_STD = 12  # spread of grey below this: "accidental shot" (pocket, lens cap)
BAD_DARK_MEAN = 35  # mean grey below this ...
BAD_DARK_P95 = 100  # ... and the brightest 5% below this too: "too dark"
BAD_BRIGHT_FRAC = 0.5  # more than this fraction of pixels at 250 or above ...
BAD_BRIGHT_MEAN = 235  # ... or mean grey above this: "too bright"
BAD_BLUR = 25.0  # sharpness (Laplacian variance) below this: "blurry"

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

# Location: a position farther than this from the nearest city in the list
# (at sea, at the poles) has no place.
GEO_MAX_CITY_KM = 200

# Events: the owner picks the gap and the minimum in Settings, within these
# ranges; the defaults are the design's (section 5.9).
EVENT_GAP_HOURS = 6  # a gap of more than this many hours starts a new event
EVENT_GAP_HOURS_RANGE = (1, 168)
EVENT_MIN_FILES = 10  # a run of fewer files is no event
EVENT_MIN_FILES_RANGE = (2, 1000)
EVENT_THUMBS = 4  # thumbnails shown for each event ...
EVENT_THUMB_PX = 120  # ... at most this long on their longest side

# Video metadata: the most box payload read from one video, in bytes (box
# headers are read by seeking; the video data itself is never read).
MP4_META_MAX = 4 * 1024 * 1024
