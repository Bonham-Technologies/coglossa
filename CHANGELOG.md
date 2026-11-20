# Changelog

All notable changes to Coglossa are recorded here, in the style of Keep a Changelog.

## Unreleased

- Workers pick up queued jobs faster when many jobs are waiting: the job queue moves to procrastinate 3.11, which skips the lock check for jobs that have no lock. The upgrade includes a database migration that runs before the new worker starts. (CGL-106)
