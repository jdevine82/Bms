# 031: Updates (milestone 12b)

Date: 8 Nov 2026. Status: decided (1 to 4); proposed (5).

1. Updates are started by **uploading a release in the web app** (Setup → Updates, admins).
2. A **host updater agent** (`bms-updater`, private bridge only, shared token) does the snapshot, install, health check and rollback; the containers get no Proxmox rights.
3. **Releases are not signed or checked for origin**, only for being well formed. Consequence: a site admin can run any code on the site server; admin accounts must be treated as that powerful. Signed releases can be added later without changing the flow.
4. After the server's update passes, **the server pushes the release to its standby HMIs**; standalone Pis have their own upload page. Pis update with the overlay off for one boot, health-check before turning it back on, and switch back on failure.
5. Proposed: health = new version answering within 180 s, the same devices online, 60 s stable; snapshots of both containers and rollback of both (a few minutes of data lost on rollback); last 3 kept.
