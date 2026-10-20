# Open questions

## Repeated application IDs (for the data source owner)

`data/raw_applications.csv` has 196 rows. Four `application_id` values appear twice (RAW-0061, RAW-0064, RAW-0115, RAW-0163), 8 rows in total. Each pair is identical in every column, so they look like duplicate exports, but the rows come from different source systems (legacy_crm, manual_entry, new_partner_feed).

Questions:

1. Is `application_id` meant to be unique per application? If so, are these exact duplicates safe to drop?
2. Could a later revision of an application ever reuse its ID with changed values?

Until answered, the service stores every submission under its own internal ID and keeps the source ID as a non-unique field; the raw file is not deduplicated.
