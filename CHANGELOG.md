# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

- Scorer: torch is held below 2.15 so the GPU image, the offline bundle and the hosted scorer build again on CUDA 12.6 (NVIDIA driver 525 or newer, unchanged), and `make torch-wheel-check`, run in CI, fails when the locked torch has no wheel for the CPU or CUDA build (CGL-76).
- Job queue: moves to procrastinate 3.12. Fetching the next job skips the lock check for jobs that have no lock and looks locks up through a new index, so it stays quick with a long backlog, and a stalled job whose copy is already waiting is closed by the queue itself. The upgrade runs four database migrations; the last removes what older versions call to queue a job, so stop the old version before the new one starts, as step 4 of the operator guide's upgrade section does (CGL-106).
- Guide: a term or name can list other correct forms of its translation (a plural, a case ending) and of the original word, so a correct "Verträge" for "Vertrag" is no longer flagged or rewritten by the recheck; "This form is fine" on a guide flag adds the form the passage uses, the translators and the recheck are told the forms, and CSV (a "forms" column) and TBX (other accepted terms) imports read them (CGL-109).
- Formatting: a formatted passage rewritten to enforce a locked guide entry is flagged "Formatting couldn't be kept", since it downloads plain; a removed marker no longer leaves a double space; a passage whose original text looks like a marker ("<1>") keeps that text and is flagged only if it had formatting, instead of dropping the formatting of its whole chunk; and bold from a Word character style based on another style and links made with HYPERLINK fields are kept (CGL-129).
- Word download: bold, italic, underlined, superscript and subscript words and links in a Word, HTML or EPUB file come back on the matching translated words; a passage whose formatting could not be kept is plain and flagged "Formatting couldn't be kept", and `coglossa bench` counts how many passages keep their formatting per system (CGL-108).
- New flag "This passage may not have been translated": a passage copied through from the original, or written mostly in a script the target language does not use (Latin text for an Amharic or Hindi target), is flagged in the report, the passage panel, the HTML export and the Word comments; scores are unchanged, and `coglossa bench flags` measures it with a "left untranslated" error (CGL-107).
- Site: the public site lives at `https://coglossa.tech`; its canonical, Open Graph and structured-data URLs, sitemap, robots file, `security.txt` and the site checks use the new host, and `coglossa.com` redirects there (CGL-126).
- Word download: the first-page and even-page headers and footers are rebuilt as Word's own variants instead of stacking on every page, and a later section's header or footer comes at the end of the document (CGL-117).
- Guide: the Terms and Names tabs have a search box and show a hundred rows at a time, with Add and Import above the list, so a guide of thousands of terms stays quick to open and find in (CGL-116).
- Guide: a guide without a summary can be saved, and a glossary file with a term, translation or note over 2,000 characters is refused at preview with its line, so an imported guide can always be saved again (CGL-115).
- Numbers check: a lone comma or point before three digits is read the way each side's language writes it, so 1,500 translated as 1.5 is flagged (CGL-114).
- Secret scanning: the `.gitleaksignore` history entries now name the public release commit, so `gitleaks git` reports no findings on the public history (CGL-103).
- Performance: the file list, the run list and the progress stream no longer load each run's full configuration snapshot on every poll (CGL-101).
