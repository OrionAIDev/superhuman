# Spec: CSV export for the sample-widget service

**Created:** 2026-01-05

## Problem

Operators of the sample-widget service have no way to export their widget
list as CSV; they currently copy rows out of the admin UI by hand.

## Approach

Add a `GET /widgets/export.csv` endpoint that streams the current widget
table as CSV, reusing the existing pagination cursor so large tables do not
require loading the whole result set into memory.

## Out of scope

Scheduled/emailed exports; any format other than CSV.
