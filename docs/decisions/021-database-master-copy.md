# The database is the master copy of the site setup

Date: 5 Nov 2026. Status: decided.

From milestone 5 the web app and setup API change the site setup in the database, and changes go live without a restart. The config file (`devine-bms-config/0`) is the export and import format; at start-up it is imported only into an empty database. Every change is checked as a whole with the config file's checks before it is saved. Until logins exist (milestone 6), setup changes are accepted only from the server itself, and every change is written to a change log that becomes the audit log.

Spec: main spec 3.1, 5.1.1, 5.6, 7; docs/specs/m5-setup.md
