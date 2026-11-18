"""Small input checks shared by the screens and services."""
import math
import re
from datetime import datetime, timezone


class AppError(Exception):
    def __init__(self, message, code="INVALID_INPUT"):
        super().__init__(message)
        self.code = code


def text(value, name, minimum=1, maximum=2000):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise AppError(f"{name} must contain {minimum} to {maximum} characters.")
    value = value.strip()
    if any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise AppError(f"{name} contains an invalid control character.")
    return value


def integer(value, name, low=-1000000, high=1000000):
    if isinstance(value, bool) or not re.fullmatch(r"-?\d{1,9}", str(value).strip()):
        raise AppError(f"{name} must be a whole number.")
    number = int(value)
    if not low <= number <= high:
        raise AppError(f"{name} must be between {low} and {high}.")
    return number


def number(value, name, low, high):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise AppError(f"{name} must be a number.") from None
    if isinstance(value, bool) or not math.isfinite(result) or not low <= result <= high:
        raise AppError(f"{name} must be between {low} and {high}.")
    return result


def timestamp(value, name="Time"):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise AppError(f"{name}: use YYYY-MM-DDTHH:MM:SS+00:00.") from None
    if result.tzinfo is None:
        raise AppError(f"{name} must include a timezone, for example +00:00.")
    return result.astimezone(timezone.utc)


def period(start, end):
    a, b = timestamp(start, "Start time"), timestamp(end, "End time")
    if a >= b:
        raise AppError("End time must be after start time.")
    return a.isoformat(), b.isoformat()


def location(value):
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise AppError("Location must be latitude,longitude.")
    return [number(value[0], "Latitude", -90, 90), number(value[1], "Longitude", -180, 180)]


def boundary(value):
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise AppError("Boundary must be south,west,north,east.")
    s, w = location(value[:2])
    n, e = location(value[2:])
    if s >= n or w >= e:
        raise AppError("Boundary must have south < north and west < east.")
    return [s, w, n, e]


def contains(box, point):
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def overlaps(a, b):
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def distance(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, [*a, *b])
    value = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 6371 * 2 * math.asin(math.sqrt(min(1, value)))
