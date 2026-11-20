# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

- Secret scanning: the `.gitleaksignore` history entries now name the public release commit, so `gitleaks git` reports no findings on the public history (CGL-103).
- Performance: the file list, the run list and the progress stream no longer load each run's full configuration snapshot on every poll (CGL-101).
