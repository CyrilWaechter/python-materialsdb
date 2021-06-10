import datetime
from materialsdb.classes import TDateTime


def date_from_xml(days: float) -> datetime.datetime:
    """Convert days since 30.12.1899 which is materialsdb xml convention to datetime"""
    return datetime.datetime(1899, 12, 30) + datetime.timedelta(days=days)


def date_to_xml(date: datetime.datetime) -> float:
    """Convert datetime to days since 30.12.1899 which is materialsdb xml convention
    >>> datetime.timedelta(days=1).total_seconds()
    86400.0"""
    return (
        date - datetime.datetime(1899, 12, 30, tzinfo=datetime.timezone.utc)
    ).total_seconds() / 86400


def new_tdatetime():
    return TDateTime(date_to_xml(datetime.datetime.now(datetime.timezone.utc)))
