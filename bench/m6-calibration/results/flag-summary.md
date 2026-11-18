# Flag recall and precision

## Tier A

| kind | caught | total | recall |
|---|---|---|---|
| omission | 633 | 700 | 0.90 |
| number | 13 | 162 | 0.08 |
| name | 90 | 136 | 0.66 |
| negation | 403 | 683 | 0.59 |
| register | 91 | 479 | 0.19 |
| locked_term | 126 | 127 | 0.99 |
| clean (false positives) | 3 | 700 | 0.00 |

Clean false-positive rate (any flag): 0.00
Precision (at this bench's mix): 1.00
Spans with a scoring error: 60

## Tier B

| kind | caught | total | recall |
|---|---|---|---|
| omission | 652 | 700 | 0.93 |
| number | 18 | 149 | 0.12 |
| name | 86 | 104 | 0.83 |
| negation | 535 | 692 | 0.77 |
| register | 58 | 413 | 0.14 |
| locked_term | 100 | 100 | 1.00 |
| clean (false positives) | 19 | 700 | 0.03 |

Clean false-positive rate (any flag): 0.03
Precision (at this bench's mix): 0.99
Spans with a scoring error: 168

## Tier C

| kind | caught | total | recall |
|---|---|---|---|
| omission | 664 | 696 | 0.95 |
| number | 45 | 144 | 0.31 |
| name | 131 | 161 | 0.81 |
| negation | 503 | 685 | 0.73 |
| register | 6 | 350 | 0.02 |
| locked_term | 157 | 157 | 1.00 |
| clean (false positives) | 118 | 700 | 0.17 |

Clean false-positive rate (any flag): 0.17
Precision (at this bench's mix): 0.93
Spans with a scoring error: 191

Recall counts low_meaning, possible_omission or terminology_violation for meaning errors and style_drift for register; a clean span with any of them is a false positive.

The clean false-positive rate (flagged clean spans over clean spans) is the number that transfers to real use. This rate counts every flag; `bench calibrate` fits each threshold to the rate of its own flag, so this one is higher. Precision is caught injected spans over caught injected spans plus flagged clean spans; precision reflects this bench's mix of injected and clean spans (several injected spans per clean one), while real translations have far fewer errors, so real precision is lower.

Negation and register errors are written by a model and not checked, so some may not be errors at all: their recall is a lower bound. Sample injections of each kind are below, for a person to check.

A span with a scoring error (a judge, back-translation or scorer call that failed) lost some of its signals, so a flag it should have had may be missing; `bench calibrate` leaves such spans out.

Injected spans carry Agreement.UNKNOWN (a reference has no translators to agree or disagree), so meaning is computed without the agreement signal. Thresholds calibrated this way slightly understate its contribution in real runs.
