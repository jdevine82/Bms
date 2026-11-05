"""Drafts and whole-setup commits for the setup screens (milestone 5B).

The commissioning wizard (main spec 5.1.1) builds its changes on a draft that is never
saved until its verify step; the mapping table saves many points in one change.

Needs BMS_TEST_DATABASE_URL (see test_db_sync.py).
"""

import os

import pytest

from setup_helpers import stack  # noqa: F401 (stack is a fixture)

if not os.environ.get("BMS_TEST_DATABASE_URL"):
    pytest.skip("BMS_TEST_DATABASE_URL is not set", allow_module_level=True)
pytestmark = pytest.mark.db


async def test_draft_changes_are_not_saved_and_commit_checks_the_revision(stack):
    client = stack["client"]
    read = await client.get("/api/setup/config")
    revision = read.headers["x-setup-revision"]
    draft = read.json()
    assert (await client.get("/api/setup/config")).headers["x-setup-revision"] == revision  # stable

    # Step 2 of the wizard: equipment from a template, on the draft only.
    made = (await client.post("/api/setup/equip-templates/power-meter/create", json={
        "draft": draft, "name": "meter9", "dis": "Spare meter",
        "bindings": {"power": {"device": "sim_meter", "address": {
            "objectType": "holdingRegister", "address": 0, "dataType": "float32"}}}})).json()
    assert made["applied"] is False and "site_meter9_power" in {p["name"] for p in made["config"]["points"]}
    assert (await client.get("/api/setup/points/site_meter9_power")).status_code == 404
    assert (await client.get("/api/setup/config")).headers["x-setup-revision"] == revision

    # Device templates work on a draft the same way.
    applied = (await client.post("/api/setup/templates/cwt-mb328b/apply", json={
        "draft": made["config"], "device": "sim_meter", "namePrefix": "site_io_", "equip": "meter9", "only": ["di0"]})).json()
    assert applied.get("applied") is False, applied
    assert "site_io_di0" in {p["name"] for p in applied["config"]["points"]}

    other = {**draft, "site": {**draft["site"], "name": "elsewhere"}}
    assert (await client.post("/api/setup/templates/cwt-mb328b/apply", json={
        "draft": other, "device": "sim_meter", "namePrefix": "x_"})).status_code == 422

    # Go live: the edited setup in one change, checked first.
    body = {"config": applied["config"], "revision": revision, "summary": "commissioned meter9"}
    assert (await client.post("/api/setup/commit?check=true", json=body)).json()["applied"] is False
    saved = (await client.post("/api/setup/commit", json=body)).json()
    assert saved["applied"] and saved["report"]["created"]["point"] == 2
    assert (await client.get("/api/setup/points/site_meter9_power")).json()["device"] == "sim_meter"
    assert (await client.get("/api/setup/changes?limit=1")).json()[0]["summary"] == "commissioned meter9"

    # The same commit again is refused: the setup changed since that revision was read.
    stale = await client.post("/api/setup/commit", json=body)
    assert stale.status_code == 409 and "changed" in stale.json()["error"]
    assert (await client.post("/api/setup/commit", json={"config": draft})).status_code == 422


async def test_rollout_preview_of_a_version_not_saved_yet(stack):
    client = stack["client"]
    meter = {"id": "my-meter", "version": "1.0.0", "dis": "Bench meter", "tags": {"elec": True, "meter": True},
             "roles": [{"role": "power", "dis": "Power", "type": "sensor", "kind": "Number", "unit": "kW",
                        "tags": {"elec": True, "power": True}}]}
    assert (await client.post("/api/setup/equip-templates", json=meter)).json()["applied"]
    assert (await client.post("/api/setup/equip-templates/my-meter/create", json={
        "name": "meter2", "dis": "Second meter", "bindings": {"power": {"device": "sim_meter", "address": {
            "objectType": "holdingRegister", "address": 0, "dataType": "float32"}}}})).json()["applied"]
    draft = (await client.get("/api/setup/config")).json()
    draft["equipTemplates"].append({**meter, "version": "1.1.0", "roles": [{**meter["roles"][0], "dis": "Active power"}]})
    preview = (await client.post("/api/setup/equip-templates/my-meter/rollout",
                                 json={"draft": draft, "version": "1.1.0", "all": True})).json()
    assert preview["applied"] is False
    assert preview["instances"][0]["changes"] == [
        {"point": "site_meter2_power", "field": "dis", "old": "Power", "new": "Active power"}]
    versions = (await client.get("/api/setup/equip-templates/my-meter")).json()["versions"]
    assert versions == ["1.0.0"]  # nothing saved
