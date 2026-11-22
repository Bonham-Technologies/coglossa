# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

- Guide: the Terms and Names tabs have a search box and show a hundred rows at a time, with Add and Import above the list, so a guide of thousands of terms stays quick to open and find in (CGL-116).
- Guide: a guide without a summary can be saved, and a glossary file with a term, translation or note over 2,000 characters is refused at preview with its line, so an imported guide can always be saved again (CGL-115).
- Numbers check: a lone comma or point before three digits is read the way each side's language writes it, so 1,500 translated as 1.5 is flagged (CGL-114).
- Secret scanning: the `.gitleaksignore` history entries now name the public release commit, so `gitleaks git` reports no findings on the public history (CGL-103).
- Performance: the file list, the run list and the progress stream no longer load each run's full configuration snapshot on every poll (CGL-101).
