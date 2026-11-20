# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

- Secret scanning: the `.gitleaksignore` history entries now name the public release commit, so `gitleaks git` reports no findings on the public history (CGL-103).
- Workers pick up queued jobs faster when many jobs are waiting: the job queue moves to procrastinate 3.11, which skips the lock check for jobs that have no lock. The upgrade includes a database migration that runs before the new worker starts. (CGL-106)
