import pytest
from mp4util import box, mdta_meta, mp4, udta_text, write
from PIL import Image
from PIL.TiffImagePlugin import IFDRational

from photosort.metadata import camera_folder, camera_of, gps_of
from photosort.naming import fold, safe_name
from photosort.thresholds import FOLDER_NAME_MAX_BYTES

GPS_IFD = 0x8825
KOLKATA = (22.0, 34.0, 21.4), (88.0, 21.0, 50.0)  # 22°34'21.4"N 88°21'50.0"E


def jpeg(path, make=None, model=None, gps=None):
    """A JPEG with the given EXIF Make, Model and GPS IFD (any may be None)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    exif = Image.Exif()
    if make is not None:
        exif[271] = make
    if model is not None:
        exif[272] = model
    if gps is not None:
        exif[GPS_IFD] = gps
    Image.new("RGB", (8, 8), "red").save(path, "JPEG", exif=exif)
    return path


def gps(lat_ref="N", lat=KOLKATA[0], lon_ref="E", lon=KOLKATA[1]):
    return {1: lat_ref, 2: lat, 3: lon_ref, 4: lon}


# camera_of


def test_camera_from_exif_stripped(tmp_path):
    path = jpeg(tmp_path / "x.jpg", "OPPO\x00", " CPH2581 ")
    assert camera_of(path) == ("OPPO", "CPH2581")


def test_camera_model_only(tmp_path):
    assert camera_of(jpeg(tmp_path / "x.jpg", model="Pixel 7")) == ("", "Pixel 7")


def test_no_camera(tmp_path):
    assert camera_of(jpeg(tmp_path / "x.jpg")) is None
    assert camera_of(jpeg(tmp_path / "y.jpg", " \x00", "")) is None
    assert camera_of(write(tmp_path / "bad.jpg", b"not a jpeg")) is None
    assert camera_of(tmp_path / "missing.jpg") is None
    assert camera_of(write(tmp_path / "notes.txt", b"hi")) is None


def test_camera_from_apple_video(tmp_path):
    meta = mdta_meta({"com.apple.quicktime.make": "Apple",
                      "com.apple.quicktime.model": "iPhone 12"})
    assert camera_of(write(tmp_path / "v.mov", mp4(meta))) == ("Apple", "iPhone 12")


def test_camera_from_android_video(tmp_path):
    meta = mdta_meta({"com.android.manufacturer": "Google", "com.android.model": "Pixel 7 "})
    assert camera_of(write(tmp_path / "v.mp4", mp4(meta))) == ("Google", "Pixel 7")


def test_camera_from_udta_video(tmp_path):
    udta = box("udta", udta_text("©mak", "samsung") + udta_text("©mod", "SM-A525F"))
    assert camera_of(write(tmp_path / "v.mp4", mp4(udta))) == ("samsung", "SM-A525F")


def test_apple_keys_win_over_udta(tmp_path):
    meta = mdta_meta({"com.apple.quicktime.make": "Apple",
                      "com.apple.quicktime.model": "iPhone 12"})
    udta = box("udta", udta_text("©mak", "Other") + udta_text("©mod", "Thing"))
    assert camera_of(write(tmp_path / "v.mov", mp4(meta, udta))) == ("Apple", "iPhone 12")


def test_oppo_video_has_no_camera(tmp_path):
    meta = mdta_meta({"com.android.version": "13", "com.android.capture.fps": 30.0})
    assert camera_of(write(tmp_path / "v.mp4", mp4(meta))) is None


# gps_of


def test_gps_from_exif(tmp_path):
    lat, lon = gps_of(jpeg(tmp_path / "x.jpg", gps=gps()))
    assert lat == pytest.approx(22.5726, abs=1e-4)
    assert lon == pytest.approx(88.3639, abs=1e-4)


def test_gps_south_and_west_are_negative(tmp_path):
    lat, lon = gps_of(jpeg(tmp_path / "x.jpg", gps=gps("S", lon_ref="W")))
    assert lat == pytest.approx(-22.5726, abs=1e-4)
    assert lon == pytest.approx(-88.3639, abs=1e-4)


@pytest.mark.parametrize("bad", [
    gps(lat=(0.0, 0.0, 0.0), lon=(0.0, 0.0, 0.0)),  # (0, 0): a phone's "no fix"
    gps(lon=(IFDRational(88), IFDRational(21), IFDRational(1, 0))),  # zero denominator
    gps(lat=(95.0, 0.0, 0.0)),
    gps(lon=(181.0, 0.0, 0.0)),
    gps(lat_ref="X"),
    {1: "N", 2: KOLKATA[0]},  # no longitude
])
def test_bad_gps_gives_none(tmp_path, bad):
    assert gps_of(jpeg(tmp_path / "x.jpg", gps=bad)) is None


def test_no_gps(tmp_path):
    assert gps_of(jpeg(tmp_path / "x.jpg", "OPPO", "CPH2581")) is None
    assert gps_of(write(tmp_path / "bad.jpg", b"not a jpeg")) is None
    assert gps_of(tmp_path / "missing.jpg") is None


def test_gps_from_video_udta(tmp_path):
    path = write(tmp_path / "v.mp4", mp4(box("udta", udta_text("©xyz", "-33.8688+151.2093/"))))
    assert gps_of(path) == pytest.approx((-33.8688, 151.2093))


def test_gps_from_apple_video_with_altitude(tmp_path):
    meta = mdta_meta({"com.apple.quicktime.location.ISO6709": "+22.5726+088.3639+012.345/"})
    assert gps_of(write(tmp_path / "v.mov", mp4(meta))) == pytest.approx((22.5726, 88.3639))


@pytest.mark.parametrize("xyz", ["+00.0000+000.0000/", "+95.0000+088.3639/",
                                 "+22.5726+188.3639/", "junk", "", "+2234.35+08821.83/"])
def test_bad_video_gps_gives_none(tmp_path, xyz):
    path = write(tmp_path / "v.mp4", mp4(box("udta", udta_text("©xyz", xyz))))
    assert gps_of(path) is None


# camera_folder


@pytest.mark.parametrize("make, model, folder", [
    ("OPPO", "CPH2581", "OPPO CPH2581"),
    ("Apple", "Apple iPhone 12", "Apple iPhone 12"),
    ("apple", "Apple iPhone 12", "Apple iPhone 12"),
    ("", "Pixel 7", "Pixel 7"),
    ("Canon", "", "Canon"),
    ("Bad", "Photos", "_Bad Photos"),
    ("Nikon", "D3500/X", "Nikon D3500_X"),
])
def test_camera_folder(make, model, folder):
    assert camera_folder(make, model) == folder


def test_camera_folder_reserved_name_is_windows_valid():
    folder = camera_folder("CON", "")
    assert safe_name(folder) == folder
    assert fold(folder) != "con"


def test_camera_folder_long_model_fits(tmp_path):
    folder = camera_folder("Make", "অ" * 300)
    assert len(folder.encode()) <= FOLDER_NAME_MAX_BYTES
    assert folder.startswith("Make অ")
    (tmp_path / folder).mkdir()
    assert camera_folder("Make", "x" * 300) == "Make " + "x" * 95


def test_camera_folder_empty():
    assert camera_folder("", "") == ""
    assert camera_folder(" ", "  ") == ""
