# 032: Haystack API (milestone 12a-Haystack)

Date: 8 Nov 2026. Status: decided (1, 2); proposed (3).

1. This milestone is the read-only Haystack API and the model export. Importing Haystack point lists into templates is left open.
2. **Only API client accounts** can log in to the Haystack API (SCRAM). They have viewer rights and cannot log in to the web app or HMI. People's accounts cannot use the API, so personal passwords never go into scripts, and no existing account needs SCRAM keys.
3. Proposed limits and details: see `docs/specs/m12-haystack-api.md`.
