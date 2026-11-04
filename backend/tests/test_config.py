import copy
import json
from pathlib import Path

import pytest

from devine_bms.config.model import json_schema
from devine_bms.config.validate import ConfigError, Severity, load_config, parse_config
from devine_bms.haystack.library import TagSource, load_library

EXAMPLE = Path(__file__).resolve().parents[2] / "config" / "site.example.json"


@pytest.fixture
def data():
    return json.loads(EXAMPLE.read_text())


def errors_of(data) -> list[str]:
    with pytest.raises(ConfigError) as exc:
        parse_config(data)
    return [str(i) for i in exc.value.issues]


def test_example_config_is_valid():
    config, issues = load_config(EXAMPLE)
    assert len(config.points) == 5
    assert issues == []


def test_library_sources():
    lib = load_library()
    assert lib.source_of("discharge") == TagSource.HAYSTACK
    assert lib.source_of("refrigRef") == TagSource.HAYSTACK
    assert lib.source_of("devineName") == TagSource.HOUSE
    assert lib.source_of("myOddTag") == TagSource.CUSTOM
    assert "supply" not in lib.haystack_tags  # Haystack 4 calls supply air "discharge"


def test_wrong_schema_version(data):
    data["schema"] = "devine-bms-config/9"
    assert any("schema" in e for e in errors_of(data))


def test_bad_name(data):
    data["points"][0]["name"] = "Site Meter Power"
    assert errors_of(data)


def test_duplicate_point_name(data):
    data["points"].append(copy.deepcopy(data["points"][0]))
    assert any("duplicate name" in e for e in errors_of(data))


def test_missing_references(data):
    data["points"][0]["device"] = "nope"
    data["points"][1]["equip"] = "nope"
    errors = errors_of(data)
    assert any("device 'nope'" in e for e in errors)
    assert any("equip 'nope'" in e for e in errors)


def test_point_needs_equip_or_space(data):
    del data["points"][0]["equip"]
    assert any("must belong to an equipment or a space" in e for e in errors_of(data))


def test_part_of_loop(data):
    data["equips"] += [
        {"name": "a", "dis": "A", "parent": "b", "tags": {"ahu": True}},
        {"name": "b", "dis": "B", "parent": "a", "tags": {"ahu": True}},
    ]
    assert any("loop" in e for e in errors_of(data))


def test_part_of_depth(data):
    data["equips"] += [
        {"name": "l1", "dis": "1", "tags": {"ahu": True}},
        {"name": "l2", "dis": "2", "parent": "l1", "tags": {"fan": True}},
        {"name": "l3", "dis": "3", "parent": "l2", "tags": {"motor": True}},
        {"name": "l4", "dis": "4", "parent": "l3", "tags": {"motor": True}},
    ]
    assert any("levels deep" in e for e in errors_of(data))


def test_feeds(data):
    data["spaces"].append({"name": "office", "dis": "Office"})
    data["feeds"] = [{"from": "meter1", "to": "office", "medium": "elec"}]
    parse_config(data)
    data["feeds"] = [{"from": "meter1", "to": "nowhere", "medium": "elec"}]
    assert any("'to' 'nowhere'" in e for e in errors_of(data))


def test_point_type_markers_are_not_tags(data):
    data["points"][0]["tags"]["sensor"] = True
    assert any("point type comes from 'type'" in e for e in errors_of(data))


def test_reserved_tags(data):
    data["points"][0]["tags"]["equipRef"] = "x"
    assert any("set by the BMS" in e for e in errors_of(data))


def test_bit_rules(data):
    data["points"][0]["address"]["dataType"] = "bit"
    assert any("data type 'bit'" in e for e in errors_of(data))


def test_writable_point_on_read_only_object(data):
    data["points"][0]["type"] = "sp"
    data["points"][0]["address"]["objectType"] = "inputRegister"
    assert any("read-only" in e for e in errors_of(data))


def test_str_needs_enum(data):
    data["points"][0]["kind"] = "Str"
    assert any("enum" in e for e in errors_of(data))


def test_custom_tag_and_name_pattern_are_warnings(data):
    data["points"][0]["tags"]["myOddTag"] = True
    data["points"][0]["name"] = "meter_power"
    _, issues = parse_config(data)
    assert {i.severity for i in issues} == {Severity.WARNING}
    text = " ".join(str(i) for i in issues)
    assert "myOddTag" in text
    assert "site_meter1_" in text


def test_json_schema_is_published():
    schema = json_schema()
    assert schema["$id"] == "devine-bms-config/0"
    assert "points" in schema["properties"]


def test_published_schema_file_is_up_to_date():
    """config/schema/devine-bms-config-0.schema.json must match the code. To refresh it, run:
    uv run python -m devine_bms.config.write_schema
    """
    published = EXAMPLE.parent / "schema" / "devine-bms-config-0.schema.json"
    assert json.loads(published.read_text()) == json_schema()


def test_rs485_example_config_is_valid():
    config, issues = load_config(EXAMPLE.parent / "site.example-rtu.json")
    assert [b.type for b in config.buses] == ["modbus_rtu_over_tcp"]
    assert len(config.devices) == 2 and issues == []


def test_bacnet_example_config_is_valid():
    config, issues = load_config(EXAMPLE.parent / "site.example-bacnet.json")
    assert config.devices[0].protocol == "bacnet_ip" and issues == []
