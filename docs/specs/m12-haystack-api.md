# Milestone 12a-Haystack: the Haystack API and model export

Status: **built** (8 Nov 2026). Main spec: 5.12 (Haystack API), 5.2 (data model), 7 (accounts), 8 (networking), 16 (Haystack conformance tests), 18 (item 12a), open item 39.

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | Scope | **The API and the model export.** Importing Haystack-tagged point lists into templates stays an open item. |
| 2 | Which accounts can use the API | **API client accounts only.** These are a new account type made by an admin: viewer rights, no web app or HMI login. People's accounts cannot use the API, so no person's password ends up in a script. |
| 3 | Details (proposed) | See "Proposed details". |

## The API

- **Base URI:** `https://<site>/api/haystack` (over Tailscale; LAN access follows the allowed networks).
- **Off by default.** An admin switches it on in Setup → Security. While it is off, every request answers 404.
- **Operations:** `about`, `ops`, `formats`, `read` (by `filter` and `limit`, or by `id`s), `nav`, `hisRead` (one id, or several with the range in the grid meta), `watchSub`, `watchPoll`, `watchUnsub`. They take GET with query parameters or POST with a grid. `pointWrite`, `hisWrite` and `invokeAction` are not offered.
- **Formats:**
  - Haystack JSON (Hayson, `application/json`) and Zinc (`text/zinc`), chosen by `Accept`.
  - Request grids are read from either.
  - Errors are error grids (`err`, `dis`) with HTTP 200, as the Haystack HTTP API says; authentication errors are 401 and 403.
- **What is served:**
  - The site, rooms (`space`), equipment and points with their tags (5.2).
  - Ids are `@<uuid>` with `dis`.
  - Refs: `siteRef`, `spaceRef`, `equipRef` (point to equipment, child to parent equipment), and the feed refs on the thing fed (`airRef`, `elecRef`, …).
  - `devineName` on points; `tz`; `unit`; `enum`.
  - `writable` on points that can be commanded (a description only: nothing can be written).
  - `his` on logged points; custom tags as plain tags.
  - Live: `curStatus` (ok, stale, fault, down, disabled, unknown), and `curVal` only when the value is Good (5.2.1).
- **History:** `hisRead` reads the history table (5.7). The range is `today`, `yesterday`, a date, two dates, or one or two date-times. Rows that are not Good are left out (gaps stay gaps).

## Login (Haystack authentication, SCRAM SHA-256)

- The HELLO → SCRAM → BEARER exchange on `/api/haystack/about`, per the Haystack auth spec and RFC 5802.
- The server keeps a salted SCRAM key pair (PBKDF2-SHA-256) for each API client, made when its password is set. It never keeps the password itself.
- An unknown user name gets a made-up salt, so the exchange does not reveal which accounts exist.
- Lockout, the login rate limit and allowed networks apply, as for web logins.
- Every API login is audit-logged as `api-login` (and failures as `login-failed`).
- An API token works only on `/api/haystack`, never as a web session. A web session does not work on `/api/haystack`.

## Model export

- Setup → Site → **Export Haystack model**, as Haystack JSON or Zinc: the site, rooms, equipment and points with their tags, no live values or history (admins).

## Proposed details (decision 3)

- `hisRead`: at most 31 days and 100,000 rows a request, at most 20 ids in a batch.
- Watches:
  - at most 10 a client, 1,000 points each;
  - lease 5 minutes by default, 1 hour at most.
- Requests: 30 every 10 seconds a client.
- API tokens: end after 60 minutes unused.
- SCRAM: 10,000 iterations.
- Standby HMIs do not serve the API (main spec 5.12: the server only). A standalone Pi does.
- Conformance tests use `phable` (MIT, a Haystack client library), as a test-only dependency.

## What was built

- **`haystack/kinds.py` and `codec.py`:** the Haystack 4 kinds, Haystack JSON (Hayson) and Zinc, both read and write.
- **`haystack/filter.py`:** the filter language (`and`, `or`, `not`, comparisons, `->` paths).
- **`haystack/model.py`:**
  - the entities from the setup tables;
  - live values from the cache;
  - feed refs on the thing fed (one feeder a medium: Haystack refs hold one value).
- **`haystack/scram.py`:** PBKDF2-SHA-256 keys and the proof check. It sends standard base64 (what clients such as phable read) and accepts both alphabets. The salt is 18 bytes, so it needs no padding.
- **`haystack/server.py`:**
  - the HELLO / SCRAM / BEARER exchange; an unknown user name gets a made-up salt;
  - the operations, plus `close` (logs out);
  - watches with leases;
  - the limits from decision 3.
- **`api/haystack_api.py`:** `/api/haystack/<op>` (GET or POST, JSON or Zinc by `Accept`) and `/api/haystack-export`. The web login guard lets `/api/haystack/` through, because it authenticates itself.
- **Accounts:**
  - migration 0015 adds `app_user.api_client` and `scram`;
  - an API client is always a viewer and never a platform account;
  - it cannot use web or PIN login, and API tokens are refused as web sessions;
  - `api-login` audit entries;
  - `haystackApi` in the security settings.
- **Web:**
  - Users: "Haystack API client" accounts, marked in the list;
  - Security: the switch and the address;
  - Site: Export Haystack JSON or Zinc;
  - Audit: the `api-login` filter.
- **Tests:**
  - `tests/test_haystack_codec.py` (35): every kind both ways, Zinc from other tools, filters, ranges, the SCRAM maths.
  - `tests/test_haystack_api.py` (5): end to end with **phable** in JSON and Zinc. It covers: off by default; people refused; tokens not sessions; read, filters, paths and limit; read by id; nav; hisRead single and batch; watches; error grids; an unknown id. It also checks every entity against the 4.0.0 and house libraries, the export, the hisRead limits and the rate limit.
  - `web/tests/haystack.mjs` (12).

## How to set up

1. Setup → Users → Add: tick **Haystack API client**, give the program a name, a user name and a password of at least the site's minimum length.
2. Setup → Security: tick **Haystack API on** and save. The address is shown: `https://<site>/api/haystack` over Tailscale. On the LAN, `http://<server>/api/haystack` follows the allowed networks.
3. In the tool (SkySpark, an energy platform, a script), set the server URI, the user name and the password. For example, with phable:

   ```python
   from phable import open_haystack_client

   with open_haystack_client("https://<site>/api/haystack", "analytics", "<password>") as c:
       print(c.read_all("point and his"))
   ```

4. Check the audit log for `api-login` entries. To stop access: switch the API off, or disable the API client account.
