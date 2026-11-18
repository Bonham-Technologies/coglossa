# Bench: flores200 devtest, 100 sentences, seed 1

| system | pair | MetricX ↓ | scored/units | too long src/out | chrF ↑ | tokens in | tokens out | seconds | errors |
|---|---|---|---|---|---|---|---|---|---|
| qwen-tuned-baseline | ar>en | 2.10 | 100/100 | 0/0 | 66.1 | 7371 | 6234 | 27 | 0 |
| qwen-tuned-pipeline | ar>en | 2.07 | 85/86 | 1/0 | 66.7 | 94723 | 44384 | 207 | 0 |
| qwen-tuned-baseline | en>am | 14.28 | 100/100 | 0/0 | 10.4 | 6370 | 9869 | 28 | 0 |
| qwen-tuned-pipeline | en>am | 5.70 | 99/99 | 0/0 | 27.4 | 133900 | 122525 | 371 | 1 |
| qwen-tuned-baseline | en>ha | 6.28 | 100/100 | 0/0 | 43.5 | 6364 | 8515 | 32 | 0 |
| qwen-tuned-pipeline | en>ha | 7.67 | 75/78 | 3/0 | 41.6 | 83981 | 51664 | 285 | 1 |
| qwen-tuned-baseline | en>sw | 5.22 | 100/100 | 0/0 | 56.9 | 6370 | 8273 | 32 | 0 |
| qwen-tuned-pipeline | en>sw | 4.97 | 100/100 | 0/0 | 56.2 | 78652 | 58480 | 198 | 1 |
| qwen-tuned-baseline | en>tr | 2.97 | 100/100 | 0/0 | 58.6 | 6358 | 7812 | 35 | 0 |
| qwen-tuned-pipeline | en>tr | 2.88 | 99/99 | 0/0 | 57.7 | 89110 | 46775 | 198 | 0 |
| qwen-tuned-baseline | en>yo | 9.71 | 100/100 | 0/0 | 25.2 | 6364 | 9834 | 43 | 0 |
| qwen-tuned-pipeline | en>yo | 9.33 | 100/100 | 0/0 | 26.9 | 105546 | 66853 | 248 | 2 |

MetricX-24 reference mode: error 0-25, lower is better. chrF: 0-100, higher is better.

So that systems compare, a pair's MetricX is the mean over the units every system of the pair scored (matched by their sentences), weighted by sentence count; summary.json also holds each system's own mean (`metricx_mean`). scored/units is each system's own count. A unit too long for MetricX has no score: "src" counts units too long by their source and reference, "out" those too long by the system's output. Units every system scored: ar>en 79, en>am 98, en>ha 71, en>sw 100, en>tr 98, en>yo 100.
