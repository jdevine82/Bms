"""Haystack JSON and Zinc, filters, hisRead ranges and the SCRAM keys (milestone 12a-Haystack).
No database needed."""

from __future__ import annotations

import base64
import hashlib
import hmac
import math
from datetime import UTC, date, datetime, time

import pytest

from devine_bms.haystack import codec, scram
from devine_bms.haystack.filter import FilterError, matches, parse
from devine_bms.haystack.kinds import MARKER, NA, REMOVE, Coord, Grid, Number, Ref, Uri, XStr
from devine_bms.haystack.server import OpError, parse_range

VALUES = [None, True, False, MARKER, REMOVE, NA, "plain", 'quote " back \\ dollar $ new\nline', Number(21.5, "°C"),
          Number(-3.25e-7), Number(42), Number(math.inf), Number(-math.inf), Ref("abc-1"), Ref("x:y.z~1", "A dis"),
          Uri("http://x/y`z"), date(2026, 11, 8), time(13, 45, 7), Coord(-33.87, 151.21), XStr("Bin", "text/plain"),
          [Number(1), "a", MARKER], {"m": MARKER, "n": Number(2, "kW")},
          codec.to_datetime(datetime(2026, 7, 1, 0, 0, tzinfo=UTC), "Sydney"),
          codec.to_datetime(datetime(2026, 1, 1, 12, 30, 0, 250000, tzinfo=UTC), "UTC")]


@pytest.mark.parametrize("fmt", [codec.JSON, codec.ZINC])
def test_every_kind_round_trips(fmt):
    rows = [{f"v{i}": v for i, v in enumerate(VALUES) if v is not None}]
    g = Grid.of(rows, {"site": MARKER, "dis": "meta"})
    back = codec.decode(codec.encode(g, fmt).encode(), fmt)
    assert back.meta == {"site": MARKER, "dis": "meta"}
    assert back.rows == rows


def test_nan_round_trips():
    for fmt in (codec.JSON, codec.ZINC):
        back = codec.decode(codec.encode(Grid.of([{"n": Number(math.nan)}]), fmt).encode(), fmt)
        assert math.isnan(back.rows[0]["n"].val)


def test_zinc_as_written_by_other_tools():
    text = ('ver:"3.0" hisStart:2026-11-08T00:00:00+11:00 Sydney database:"x"\n'
            'id dis:"Id",dis,curVal,area,enabled,tags\n'
            '@p1 "Room temp","Room temp",21.5°C,1_000m²,T,{point sensor}\n'
            '@p2,"x",,N,F,\n')
    g = codec.grid_from_zinc(text)
    assert g.meta["hisStart"].tz == "Sydney" and g.meta["hisStart"].val.utcoffset().total_seconds() == 11 * 3600
    assert g.cols[0] == ("id", {"dis": "Id"})
    assert g.rows[0] == {"id": Ref("p1"), "dis": "Room temp", "curVal": Number(21.5, "°C"),
                         "area": Number(1000, "m²"), "enabled": True, "tags": {"point": MARKER, "sensor": MARKER}}
    assert g.rows[0]["id"].dis == "Room temp"
    assert g.rows[1] == {"id": Ref("p2"), "dis": "x", "enabled": False}


@pytest.mark.parametrize("bad", ['ver:"9.0"\na\n', "not zinc", 'ver:"3.0"\na\n"unterminated\n'])
def test_bad_zinc_is_refused(bad):
    with pytest.raises(codec.DecodeError):
        codec.grid_from_zinc(bad)


def test_requests_in_either_format():
    assert codec.decode(b'{"_kind":"grid","meta":{"ver":"3.0"},"cols":[{"name":"filter"}],"rows":[{"filter":"point"}]}',
                        "application/json").rows == [{"filter": "point"}]
    assert codec.decode(b'ver:"3.0"\nfilter\n"point"\n', "text/zinc").rows == [{"filter": "point"}]
    assert codec.decode(b'ver:"3.0"\nfilter\n"site"\n', None).rows == [{"filter": "site"}]
    assert codec.decode(b"", "text/zinc").rows == []
    assert codec.choose("text/zinc") == codec.ZINC and codec.choose("application/json") == codec.JSON
    assert codec.choose(None) == codec.JSON


# ---------- filters ----------

ENTS = {
    "s": {"id": Ref("s"), "site": MARKER, "dis": "Site", "area": Number(500, "m²")},
    "e": {"id": Ref("e"), "equip": MARKER, "ahu": MARKER, "siteRef": Ref("s"), "dis": "AHU-1"},
    "p": {"id": Ref("p"), "point": MARKER, "sensor": MARKER, "temp": MARKER, "kind": "Number", "unit": "°C",
          "equipRef": Ref("e"), "siteRef": Ref("s"), "curVal": Number(21.5, "°C"), "dis": "Supply temp",
          "his": MARKER, "since": date(2026, 1, 1)},
    "q": {"id": Ref("q"), "point": MARKER, "cmd": MARKER, "kind": "Bool", "equipRef": Ref("e"), "curVal": True},
}


def ids(text):
    node = parse(text)
    return sorted(k for k, e in ENTS.items() if matches(node, e, ENTS.get))


@pytest.mark.parametrize("text, expected", [
    ("point", ["p", "q"]),
    ("point and sensor", ["p"]),
    ("point and not sensor", ["q"]),
    ("site or equip", ["e", "s"]),
    ("point and (sensor or cmd) and kind == \"Bool\"", ["q"]),
    ("curVal > 20°C", ["p"]),
    ("curVal > 20", ["p"]),
    ("curVal > 20kW", []),
    ("curVal == true", ["q"]),
    ("curVal != true", ["p"]),  # a missing tag is never compared
    ("equipRef == @e", ["p", "q"]),
    ("equipRef->ahu", ["p", "q"]),
    ("equipRef->siteRef->area >= 500m²", ["p", "q"]),
    ("equipRef->siteRef->area > 500m²", []),
    ("dis < \"B\"", ["e"]),
    ("since <= 2026-02-01", ["p"]),
    ("not his", ["e", "q", "s"]),
])
def test_filters(text, expected):
    assert ids(text) == expected


@pytest.mark.parametrize("bad", ["", "point and", "point ==", "(point", "point == site", "a -> ", "point ~ 1"])
def test_bad_filters_are_refused(bad):
    with pytest.raises((FilterError, ValueError)):
        parse(bad)


# ---------- hisRead ranges ----------

def test_ranges():
    s, e = parse_range("2026-11-08", "Sydney")
    assert s.isoformat() == "2026-11-08T00:00:00+11:00" and e.isoformat() == "2026-11-09T00:00:00+11:00"
    s, e = parse_range("2026-11-01,2026-11-08", "Sydney")
    assert (e - s).days == 8
    s, e = parse_range("2026-11-08T01:00:00+11:00 Sydney,2026-11-08T03:00:00+11:00 Sydney", "Sydney")
    assert (e - s).total_seconds() == 7200
    s, e = parse_range(date(2026, 11, 8), "Sydney")
    assert (e - s).days == 1
    s, e = parse_range("today", "UTC")
    assert (e - s).days == 1
    with pytest.raises(OpError):
        parse_range("2026-11-08,2026-11-01", "Sydney")
    with pytest.raises((OpError, ValueError)):
        parse_range("last week", "Sydney")


# ---------- SCRAM ----------

def test_scram_keys_match_rfc_5802_maths():
    """The client side computed as RFC 5802 (and phable) does."""
    keys = scram.make_keys("a long enough pw", b"0123456789abcdefgh")
    assert len(keys["salt"]) == 24 and "=" not in keys["salt"]
    salted = hashlib.pbkdf2_hmac("sha256", b"a long enough pw", base64.urlsafe_b64decode(keys["salt"]), keys["i"])
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored = hashlib.sha256(client_key).digest()
    msg = "n=api1,r=abc,r=abcdef,s=x,i=10000,c=biws,r=abcdef"
    proof = bytes(a ^ b for a, b in zip(client_key, hmac.new(stored, msg.encode(), hashlib.sha256).digest(), strict=True))
    assert scram.check_proof(keys, msg, base64.b64encode(proof).decode())
    assert not scram.check_proof(keys, msg + "x", base64.b64encode(proof).decode())
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    assert scram.server_signature(keys, msg) == base64.b64encode(
        hmac.new(server_key, msg.encode(), hashlib.sha256).digest()).decode()
    assert scram.unb64(scram.b64(b"\xfb\xff\xfe")) == b"\xfb\xff\xfe"
    assert scram.unb64("-_-_") == base64.urlsafe_b64decode("-_-_")


def test_unknown_users_get_the_same_made_up_salt():
    a, b = scram.fake_keys(b"k" * 32, "nobody"), scram.fake_keys(b"k" * 32, "nobody")
    assert a["salt"] == b["salt"] and a["i"] == scram.ITERATIONS
    assert not scram.check_proof(a, "m", scram.b64(b"x" * 32))
