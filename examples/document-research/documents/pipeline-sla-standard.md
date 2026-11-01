# Pipeline Service Level Standard (Sample)

Pipelines are classified into gold, silver and bronze tiers.

- Gold tier pipelines have a freshness SLA of 2 hours and page the on-call data engineer on failure.
- Silver tier pipelines have a freshness SLA of 6 hours and create a ticket on failure.
- Bronze tier pipelines have a freshness SLA of 24 hours and are reviewed during business hours.

A failed gold tier run must be triaged within 30 minutes. Restarting a gold tier pipeline in production requires
approval from the pipeline owner.
