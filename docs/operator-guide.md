# Coglossa operator guide

## 1. What Coglossa is

Coglossa translates documents with a team of AI agents that run on your own model servers, then scores each passage and flags the ones a person should check. It runs entirely on one Linux host under Docker and never needs the internet.

### The bundle

```
coglossa-<version>/
  install.sh  backup.sh  restore.sh
  compose.yaml  compose.gpu.yaml  compose.demo.yaml
  config/coglossa.example.yaml  config/coglossa.demo.yaml  config/tiers.example.yaml  config/bench.example.yaml
  images/app.tar  images/scorer.tar  images/postgres.tar
  models/metricx-24-hybrid-xl-v2p6/  models/metricx-24-hybrid-large-v2p6/  models/mt5-xl/  models/labse/  models/weights.json
  bench/flores200/flores200_dataset/  bench/flores200/LICENSE.txt  bench/flores200/ATTRIBUTION.txt
  sbom/app.cdx.json  sbom/scorer.cdx.json  sbom/postgres.cdx.json  sbom/web.cdx.json
  manifest.json  VERSION  SHA256SUMS  operator-guide.md
```

| Entry | What it is |
| --- | --- |
| `install.sh` | Checks the bundle, loads the images, creates your config and `.env`. |
| `backup.sh` | Saves the database and the uploaded files. |
| `restore.sh` | Puts a backup back, replacing all current data. |
| `compose.yaml` | The services: database, scorer, worker, API with the web app. |
| `compose.gpu.yaml` | Adds the NVIDIA GPU to the scorer and selects MetricX XL. |
| `compose.demo.yaml` | Replaces the model servers and scorer with synthetic ones, to try the stack. |
| `config/coglossa.example.yaml` | The template that becomes `config/coglossa.yaml`. |
| `config/coglossa.demo.yaml` | The configuration the demo uses. |
| `config/tiers.example.yaml` | The shipped reliability tiers per language, which become `config/tiers.yaml` (section 5). |
| `config/bench.example.yaml` | The template for a benchmark of your models (section 12). |
| `images/*.tar` | The Docker images: app (about 415 MB), scorer (about 2 GB CPU build, about 11.3 GB CUDA build), PostgreSQL. |
| `bench/flores200/` | FLORES-200, the test sentences for benchmarking (section 12), with its licence and attribution. Only in a bundle built with it (`manifest.json` says `"bench": {"flores200": true}`). |
| `models/` | The scoring models (MetricX XL 14 GB, MetricX large 4.6 GB, the mT5-XL tokenizer 4 MB, LaBSE 1.8 GB) and `weights.json` listing them. |
| `sbom/` | Software bills of materials, one per image plus the web app. |
| `manifest.json` | Image ids and the weights' Hugging Face revisions. |
| `VERSION` | The bundle's version. |
| `SHA256SUMS` | Checksums of every file in the bundle. |
| `operator-guide.md` | This guide. |

A bundle with the CUDA scorer is roughly 35 GB.

The scorer image's Python packages, PyTorch and its CUDA libraries included, are exactly the ones listed in the source's `scorer/uv.lock`, and so is the build tool that turned the scorer's own source into a package (it is used while the image is built and is not in the image). When the image was built, each one was checked against the checksum recorded there, and each came from a single source: PyTorch from download.pytorch.org, everything else from PyPI.

## 2. Requirements

- **Host:** Linux x86_64 with Docker Engine 24 or newer and the Compose plugin 2.24 or newer. Check with `docker compose version`.
- **Software on the host:** bash, coreutils and tar.
- **Disk:** twice the bundle's size during the install, plus the images Docker loads from it (about 3 GB with the CPU scorer, about 12 GB with the CUDA scorer), plus room for your documents and backups.
- **Memory:** at least 16 GB with the scorer on CPU (MetricX large, the default). 32 GB to run MetricX XL on CPU.
- **GPU (optional):** an NVIDIA GPU with at least 24 GB of memory for MetricX XL, the NVIDIA driver 525 or newer (the scorer image is built for CUDA 12.6), and the NVIDIA Container Toolkit.
- **Network:** the host must reach your model servers. Coglossa never needs the internet.

## 3. Install

1. Check the copy, then unpack it:

   ```sh
   sha256sum -c coglossa-<version>-linux-amd64.tar.sha256
   tar -xf coglossa-<version>-linux-amd64.tar
   cd coglossa-<version>
   ```

2. Run the installer (add `--gpu` to run the scorer on an NVIDIA GPU):

   ```sh
   ./install.sh
   ```

   It checks every file against `SHA256SUMS`, checks for Docker Compose 2.24 or newer, loads the three images into Docker, and creates three files if they do not exist: `config/coglossa.yaml` (a copy of the example), `config/tiers.yaml` (a copy of `config/tiers.example.yaml`) and `.env` (a random database password, readable only by you). It never overwrites them, so it is safe to run again; they are not in `SHA256SUMS`, so your edits never fail the check. It loads only the image archives that `SHA256SUMS` lists: any other `.tar` in `images/` was not checked, so it says so and skips it. `--gpu` also writes `COMPOSE_FILE=compose.yaml:compose.gpu.yaml` into `.env`. A plain `COMPOSE_FILE=compose.yaml` there is replaced; if `.env` sets `COMPOSE_FILE` to anything else without `compose.gpu.yaml`, it changes nothing and stops, naming the line to write; add `:compose.gpu.yaml` to that line and run `./install.sh --gpu` again. Keep `.env`: the database was created with that password.

   It also makes `config/`, `models/` and `bench/` world-readable (`chmod -R a+rX`), because the containers read them as uid 10001 and a strict umask (027, 077) would hide them; none holds a secret, since backend keys go in `.env`.

3. Edit `config/coglossa.yaml`. It has three parts that matter:

   - **`backends`**: your model servers. Each has a `base_url` ending in `/v1`, a `max_concurrency` (never more simultaneous requests than this) and a `timeout_s`: the most time one request may take as a whole, from sending it to the last word of the answer (a server that answers slowly is stopped at that limit, then retried). Time spent waiting for a free slot under `max_concurrency` does not count. Any OpenAI-compatible server works (vLLM, Ollama, TGI, llama.cpp server, LM Studio). Coglossa asks for each answer as a stream, so that it can stop a model that keeps repeating itself (see below, after `extra_body`); `timeout_s` covers a streamed answer in the same way. A server that cannot stream is noticed on its first answer and asked without streaming from then on, for that model, until Coglossa restarts; one that streams but refuses to add token counts to the stream (`stream_options`) is asked to stream without them, and its calls then show no token counts. Set `stream: false` on a backend to never ask it to stream, for example behind a proxy that mishandles streamed answers; Coglossa then cannot stop a loop early.
   - **`models`**: a name you choose, the `backend` it runs on, and `name`, the model id exactly as the server lists it. Find it with:

     ```sh
     curl http://<server>:<port>/v1/models
     ```

     Also set `context_window` and `structured_output` (`json_schema`, `json_object` or `none`, whichever the server supports).
   - **`roles`**: which model does each job: `literal_translator`, `contextual_translator`, `reviewer`, `profiler_guide`, and optionally `judge` and `roundtrip`.

   Give the `judge` and the `roundtrip` a model from a different family than the translators and the reviewer. The judge scores errors and must differ from the reviewer's model; the roundtrip back-translates and must differ from every translator's. A model that checks its own work shares its blind spots, so the scores would be flattering rather than useful. Without a judge there is no style score, and passages keep a lower reliability tier.

4. Start it and check it:

   ```sh
   docker compose up -d --wait
   docker compose exec api coglossa doctor
   ```

   Every check in `doctor` must pass. A warning explains itself and says what to do.

5. Open `http://<host>:8080` in a browser.

The first start of the scorer takes minutes, because it loads MetricX into memory. Until then `docker compose ps` shows it as `starting`, and `up -d --wait` waits for it. With `up`, the worker starts only after the scorer is healthy. The scorer has 15 minutes to load before Docker marks it unhealthy and `up -d --wait` fails. On a slow CPU host, or with MetricX XL on CPU, give it longer in `.env`, for example `COGLOSSA_SCORER_START_PERIOD=30m`, and raise `worker.scorer_wait_s` to match, 1800 for 30m (below).

After a reboot, Docker starts every service at once. The worker then waits for the scorer itself: it checks the scorer every 5 seconds for up to 15 minutes (`worker.scorer_wait_s` in `config/coglossa.yaml`, default 900; 0 turns the wait off), and logs one line while it waits. If the scorer is still not healthy after that, the worker starts anyway and logs a warning. A one-off `coglossa worker --burst` does not wait by default: it checks the scorer once and, if it is not reachable, starts at once and logs a warning (`worker.burst_scorer_wait_s`, default 0, sets a wait for it). Passages translated until the scorer is back get no model-based scores (see section 11, "Runs finish with no scores").

## 4. Try it first (demo)

The demo needs no model servers. It uses synthetic models and scores, so the translations are placeholders.

```sh
docker compose -p coglossa-demo -f compose.yaml -f compose.demo.yaml up -d --wait
```

The `-p coglossa-demo` gives the demo its own project name, so its data stays apart from the real installation's and the two never mix. Open `http://<host>:8080`. If you set `COMPOSE_FILE` in `.env` (for example with `--gpu`), pass `-f` as shown anyway: `-f` takes precedence.

Stop it with the same `-f` flags:

```sh
docker compose -p coglossa-demo -f compose.yaml -f compose.demo.yaml down       # keeps the demo data
docker compose -p coglossa-demo -f compose.yaml -f compose.demo.yaml down -v    # deletes the demo data
```

Stop the demo before you start the real installation: both use port 8080 (or set `COGLOSSA_HTTP_PORT` for one of them).

## 5. Configuration

After any change, apply it and run `doctor` (see "Applying changes" below).

### A model server on the same host

Use `http://host.docker.internal:<port>/v1` as the backend's `base_url`. Ollama listens only on 127.0.0.1 by default, which containers cannot reach; start it with `OLLAMA_HOST=0.0.0.0`.

### Model server options

Some models think before they answer. This costs many more tokens and time, and can cut the answer short. Most servers let you turn thinking off with an option in the request. Put that option under `extra_body` in the model's entry. Coglossa adds it to every request to that model.

```yaml
models:
  qwen-vllm:
    backend: gpu-a
    name: Qwen/Qwen3-32B
    context_window: 32768
    extra_body:
      chat_template_kwargs: { enable_thinking: false }   # vLLM serving Qwen3
  qwen-openrouter:
    backend: cloud
    name: qwen/qwen3.6-35b-a3b
    context_window: 32768
    extra_body:
      reasoning: { enabled: false }                      # OpenRouter
```

The server must understand the options. Coglossa does not check them, and a server may ignore or reject ones it does not know. You cannot set `model`, `messages`, `temperature`, `max_tokens`, `response_format` or `stream` here. Coglossa sets those itself; set a model's `temperature` (default 0.2) and `max_output_tokens` in its entry, and turn streaming off with the backend's `stream`.

Some models occasionally repeat a phrase until they reach their output limit. In the October 2026 benchmark, Qwen3.6 did this on low-resource languages, and its recommended sampling against it (`temperature: 0.7` with `presence_penalty: 1.5` under `extra_body`) stopped the repetition but made it drift into English; Gemma 4 repeated itself rarely at the default settings (`docs/bench-results-2026-10.md`). Run `coglossa bench` on your own languages before you change sampling.

Coglossa watches each answer as it arrives, and the model's reasoning if the server sends it, and stops the answer once its end is one phrase repeated back to back: at least four times, over at least 1,200 characters, for a phrase of up to 1,000 characters. That is a few hundred to about a thousand tokens instead of the whole output limit. It works on characters, so it treats every script alike, with or without spaces between words. Ordinary repetition does not stop an answer: list items, table rows and JSON entries differ from one to the next, a refrain repeats a few times at most, and repetition the source has too is allowed: copied from it (a long line of dashes, say) until the answer repeats more than the source does, and translated (a form whose identical lines come back in other words) until the answer repeats a little more than twice as often as the source. A stopped answer is handled exactly like one cut off at the output limit: a translation passage is split and tried again, a guide that cannot be built falls back to a light guide, and so on. The run's report lists the call among its model calls (`llm_calls`) as failed, with the error `stopped early: the answer repeated a phrase of 52 characters 24 times in a row, 1290 characters in (a repetition loop)`. If you think the check stopped an answer that was going well, set `stream: false` on that backend and run the document again to compare. Set `max_output_tokens` too (16384 is a good start): it stops what the check cannot see, such as a loop that changes a little each time or a model on a server that does not stream, before it runs until the server times out; Coglossa then splits the passage and tries again. Coglossa also keeps passages short enough that a translation one and a half times as long still fits the limit of the translators, the reviewer and the roundtrip, whether it is their model's or their own (see "Output limits per role" below; with 16384, at most 10,922 tokens of source text), so a long passage is not cut off on every run; a limit too small for one sentence of 600 characters makes every run fail with an error that names the role and the limit to raise.

### Output limits per role

A model's `max_output_tokens` applies to every job that model does. A job under `roles` can set its own `max_output_tokens`, which replaces the model's for that job's requests. It cannot be higher than the model's: `doctor` and the services refuse such a config and name the role. To give one job more, raise the model's limit and set lower ones on the model's other jobs.

The judge has a lower limit by default: 8,192 tokens, or the model's limit if that is lower. When the model sets no limit, it is 8,192 or half the model's `context_window`, whichever is lower, so the request still leaves room for the judge's question. Its answer lists the errors in at most 20 passages with short quotes, which takes a few thousand tokens. A judge that repeats itself therefore stops after 8,192 tokens instead of running to the model's limit, and an answer that is cut off costs little: Coglossa splits the passages into smaller groups and asks again.

Leave the other jobs at the model's limit:

- The translators, the `reviewer` and the back-translation (`roundtrip`) write out the whole passage. Coglossa keeps passages short enough for the lowest of their limits, so a lower limit on any of them makes every passage shorter for all of them: each translation sees less of the text around it, and a run makes more requests.
- The translation guide (`profiler_guide`) grows with the document. When it is cut off, Coglossa builds a lighter guide instead, with less in it.

A model that thinks before it answers spends part of the limit on its thinking. Turn thinking off if the server allows it (see "Model server options" above). If you keep it on and a run's problems show the judge's answers cut off (`output truncated (finish_reason=length)` for the role `judge`), raise the judge's limit, up to the model's:

```yaml
roles:
  judge:
    model: gemma-judge
    max_output_tokens: 16384   # the default is 8192; no higher than the model's max_output_tokens
```

### API keys

If a model server needs a key, add it to `.env`:

```
GPU_A_KEY=the-key
```

and name the variable in the backend: `api_key_env: GPU_A_KEY`.

### The scorer

The scorer measures translation quality (MetricX-24) and meaning preservation (LaBSE).

- By default it runs MetricX large on the CPU.
- `./install.sh --gpu`, or `COMPOSE_FILE=compose.yaml:compose.gpu.yaml` in `.env`, runs MetricX XL on the GPU.
- `COGLOSSA_QE_MODEL` (a directory name under `models/`, for example `metricx-24-hybrid-xl-v2p6`) and `COGLOSSA_SCORER_DEVICE` (`cpu` or `cuda`) in `.env` override either choice. MetricX XL also runs on CPU on a 32 GB machine, only slower.
- `COGLOSSA_SCORER_START_PERIOD` in `.env` (default `15m`) is how long the scorer may take to load before Docker marks it unhealthy. Raise it on a slow CPU host (section 3).
- Without a working scorer, scores stay empty and reliability drops. Coglossa never invents scores.

### Language tiers

Scores are less trustworthy for some languages. `config/tiers.yaml` (created by `install.sh` from the shipped `config/tiers.example.yaml`) assigns each language a reliability tier: A for the main languages, B for well-supported ones, and C for everything else, including low-resource languages. A pair gets the worse tier of its two languages unless you list it under `pairs`. Each tier weighs the signals differently and has its own threshold. The shipped thresholds were calibrated in October 2026 so that each tier flags about one correct passage in ten; a lower tier catches fewer real errors at that rate (in the calibration, 86% in tier A, 84% in tier B and 68% in tier C), so passages in tier C deserve a closer look even when they are not flagged. The shipped tiers are defaults; check them on your own models with `coglossa bench` (section 12, "Benchmark your models").

To use your own copy, uncomment this in `config/coglossa.yaml` (the path is relative to that file):

```yaml
scoring:
  language_tiers_file: tiers.yaml
```

Then edit `config/tiers.yaml`, for example `pairs: { "en>yo": B }` if your models are strong on that pair, and apply the change. Leave `config/tiers.example.yaml` as it is: `install.sh` checks it.

The tiers file's content counts as part of the configuration: if you edit it after a document is submitted but before the worker runs it, or before you apply the change, the run still finishes, but its report lists a configuration change and it counts as complete with errors; its scores use the tiers the worker had loaded. When you upgrade, each document that was submitted but not yet run, under a config that enables a tiers file, gets that note once, even if the file was not edited; ignore it, or translate the document again.

### Storage in an S3-compatible bucket

Uploaded files live on the `files` volume by default, and an air-gapped site should keep it. Optionally, `storage.kind: s3` (see the commented lines under `storage:` in the example config) keeps them in an S3-compatible bucket instead, such as AWS S3, Supabase Storage or MinIO. Remove `path:` when you set `kind: s3`. The S3 client is the engine's optional `s3` extra (`coglossa[s3]`); the app image does not include it, so this needs an image built with it. The access key and secret come from the environment variables named by `access_key_id_env` and `secret_access_key_env` in `.env`, never from the config file. `doctor` checks the bucket by writing and deleting a small probe object. A backup (section 8) holds only the volume, so back up a bucket with the provider's own tools.

### The port

In `.env`: `COGLOSSA_HTTP_PORT` (default 8080) and `COGLOSSA_HTTP_BIND` (default 0.0.0.0, all interfaces). With named users, see section 6 before you leave it at 0.0.0.0.

### Your own prompt templates

`prompts_dir` in `config/coglossa.yaml` names a folder of your own prompt templates (`<prompt>/v<N>.j2`). For each prompt, a folder there with a template in it replaces every built-in version of that prompt, whatever their numbers. Since CGL-108, the built-in `contextual`, `literal` and `reconcile` prompts (version 2 and later) tell the models to keep the numbered markers (`<1>…</1>`) that stand for bold, italic, underlined, superscript and subscript words and links in Word, HTML and EPUB files (and, since CGL-110, PowerPoint and Excel files; since CGL-131, SRT and WebVTT subtitle files). If you override any of these three prompts, your templates get the same markers in the text without those instructions: expect many passages flagged "Formatting couldn't be kept" and plain text for them in the Word, PowerPoint, Excel and subtitle downloads. Add the built-in instruction line about markers (in the newest `engine/coglossa/prompts/templates/<prompt>/v<N>.j2`) to your templates. Since CGL-109 the built-in prompts that read the guide (version 3 of these three, version 2 of `judge` and `sweep`) also explain a guide entry's accepted forms; an override without that line still works, but its models may rewrite a correct inflected form.

### Applying changes

The services read `config/` only when they start, so a plain `docker compose up -d --wait` does not apply an edit there. After you edit `config/coglossa.yaml`, `config/tiers.yaml` or `.env`:

```sh
docker compose up -d --wait --force-recreate migrate api worker
docker compose exec api coglossa doctor
```

This restarts the migrations, the API and the worker with the new settings and waits until they are healthy. The worker finishes its current passage first (up to 2 minutes); an unfinished one is picked up again. The database and the scorer keep running, unless your `.env` change concerns them (`COGLOSSA_QE_MODEL`, for example), in which case they restart too.

With an invalid config, the command fails with `service "migrate" didn't complete successfully`, and the API and the worker stay stopped. Read the error with `docker compose logs migrate`, fix the file, and run the command again.

## 6. Users, access and the reverse proxy

With `auth: none` (the default), anyone who can reach the port can use Coglossa. That is fine on a closed network where that is acceptable.

For named users, put an authenticating reverse proxy in front. It handles TLS and sign-in, and passes the user name to Coglossa in the `X-Remote-User` header. In `config/coglossa.yaml`:

```yaml
auth: { mode: trusted_header, header: X-Remote-User }
```

Coglossa believes whatever user name a request carries, so make sure only the proxy can reach it. Otherwise anyone who can reach port 8080 could send the header themselves and be anyone. Do one of these, or both:

- **The proxy runs on this machine: bind the API to 127.0.0.1** by setting `COGLOSSA_HTTP_BIND=127.0.0.1` in `.env`. Only programs on this machine can then reach port 8080.
- **The proxy runs elsewhere, or you want a second lock: a proxy secret.** Coglossa then refuses every request that does not carry the secret in the `X-Coglossa-Proxy-Secret` header (except `/api/health`, which the health checks use). Put a long random value in `.env`, for example the output of `openssl rand -hex 32`:

  ```sh
  COGLOSSA_PROXY_SECRET=<the random value>
  ```

  Name that variable in `config/coglossa.yaml`:

  ```yaml
  auth:
    mode: trusted_header
    header: X-Remote-User
    proxy_secret_env: COGLOSSA_PROXY_SECRET
  ```

  Then have the proxy send it. In nginx, add this line to **every** `location` that forwards to Coglossa (in the example below, next to `proxy_set_header X-Remote-User`):

  ```nginx
  proxy_set_header X-Coglossa-Proxy-Secret "<the random value>";
  ```

  The value is then in the nginx config too, so let only root and nginx read that file.

Apply either change as in "Applying changes" above. If neither is in place, `coglossa-api` prints a warning when it starts (`docker compose logs api`), and `coglossa doctor` reports it under `auth` with what to set. Both go by `COGLOSSA_HTTP_BIND`: any value other than 127.0.0.1, ::1 or localhost counts as reachable from other machines.

What the proxy must do:

- **Upload size.** Set the proxy's body limit at or above `api.max_upload_mb` (200 MB by default). The API checks the size only after the whole body has arrived, so without a proxy limit a huge upload is still received.
- **Live progress.** Progress is a server-sent event stream. Turn off response buffering for `/api/runs/*/events` and allow long reads.
- **CSRF.** If your SSO proxy uses a session cookie, set it `SameSite=Lax` or `Strict`. Otherwise another website could trigger actions such as cancel or retry on a run whose id it knows.
- **TLS.** Terminate it at the proxy.
- **HTTPS only (HSTS).** Once HTTPS works for everyone, have the proxy send
  `Strict-Transport-Security`, as in the example below. It tells browsers to use only HTTPS
  for this address, so it belongs on the proxy that handles TLS, not on Coglossa. Browsers
  remember it for a year: add it only after HTTPS works, and add `includeSubDomains` only
  if every subdomain of this address has HTTPS too. With it on, browsers no longer let
  people click past a certificate warning, so an expired certificate, or one from an
  internal CA a computer doesn't trust, locks everyone out until it is fixed.
- **Groups (only with `groups_header`, see "Teams and admins").** Set the groups header on
  **every** location that forwards to Coglossa, to an empty value when the person has no
  groups. nginx does not inherit `proxy_set_header` into a block that has its own
  `proxy_set_header` lines, so a line at `server` level is not enough. A location without it
  would pass on a header the browser sent itself, which would make anyone an admin.

An nginx example:

```nginx
server {
    listen 443 ssl;
    server_name coglossa.example.internal;

    ssl_certificate     /etc/nginx/tls/coglossa.crt;
    ssl_certificate_key /etc/nginx/tls/coglossa.key;

    # At least api.max_upload_mb (200 by default).
    client_max_body_size 200m;

    # HTTPS only, for a year. Add once HTTPS works (see "HTTPS only" above). nginx ignores
    # this line in a location that has add_header lines of its own; none here do.
    add_header Strict-Transport-Security "max-age=31536000" always;

    # Progress stream: no buffering, long reads.
    location ~ ^/api/runs/[^/]+/events$ {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_read_timeout 1h;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # The authenticated user's name. $remote_user is set by auth_basic. With an SSO
        # service behind auth_request (for example oauth2-proxy, with the /oauth2/ location
        # its documentation gives), use these three lines instead, here and in `location /`:
        #     auth_request /oauth2/auth;
        #     auth_request_set $auth_user $upstream_http_x_auth_request_user;
        #     proxy_set_header X-Remote-User $auth_user;
        # With groups_header set (Teams and admins), also these two, here and in `location /`:
        #     auth_request_set $auth_groups $upstream_http_x_auth_request_groups;
        #     proxy_set_header X-Remote-Groups $auth_groups;
        # Without groups from your sign-in service, this line drops any the browser sent:
        proxy_set_header X-Remote-Groups "";
        proxy_set_header X-Remote-User $remote_user;
        # With auth.proxy_secret_env set (above):
        # proxy_set_header X-Coglossa-Proxy-Secret "<the random value>";
    }

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # Same sources of the user name and groups as above.
        proxy_set_header X-Remote-Groups "";
        proxy_set_header X-Remote-User $remote_user;
        # With auth.proxy_secret_env set (above):
        # proxy_set_header X-Coglossa-Proxy-Secret "<the random value>";
    }
}
```

### Security headers

Coglossa sets these security headers on every response, the web app's pages and files included. Your proxy does not need to add them:

| Header | Value | What it does |
|---|---|---|
| `Content-Security-Policy` | `default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self' data:; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'` | Pages load scripts, styles, fonts and images only from Coglossa itself, and no other website can show Coglossa in a frame. |
| `X-Frame-Options` | `DENY` | Stops framing in older browsers. |
| `X-Content-Type-Options` | `nosniff` | Browsers treat every file as the type it is sent as. |
| `Referrer-Policy` | `same-origin` | Coglossa addresses are never sent to other sites. |
| `Permissions-Policy` | `accelerometer=(), browsing-topics=(), camera=(), display-capture=(), geolocation=(), gyroscope=(), hid=(), magnetometer=(), microphone=(), midi=(), payment=(), serial=(), usb=(), xr-spatial-tracking=()` | Turns off browser features Coglossa never uses, such as the camera, microphone and location. |
| `Cross-Origin-Opener-Policy` | `same-origin` | Coglossa's pages share no window with other websites' pages. |
| `Cross-Origin-Resource-Policy` | `same-origin` | Other websites cannot load Coglossa's pages, files or data into their own pages. |

The HTML export gets a stricter policy (`default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; sandbox`). If someone opens the file from Coglossa's address, it can style itself and do nothing else.

If your proxy adds its own `Content-Security-Policy`, the browser enforces both policies, so yours must not be looser than Coglossa's. Otherwise, leave these headers to Coglossa. The one header your proxy should add is `Strict-Transport-Security` (see "HTTPS only" above).


### Teams and admins

With named users, people see only the sets of their teams, plus personal sets they made
themselves. Admins see every set, plus **Settings ▸ Status** and **Technical details**. Sets
from before this version have no team, so only admins see them until an admin moves each one
into a team (**⋯ ▸ Move to team…** on the set). The command line on the server keeps full
access.

Teams and admins come from two places, and a person's teams are the union of both:

- **The proxy's groups.** If your proxy can pass the person's groups (most SSO and LDAP proxies
  can), set `groups_header`. The proxy must **always set** that header, to an empty value when
  the person has no groups, so that a browser can never send it itself. Leave `groups_header`
  unset if your proxy cannot pass groups: Coglossa then ignores the header completely.
- **The people file.** `people_file` names a YAML file next to `coglossa.yaml`:

  ```yaml
  admins: [ana, sione]
  teams:
    spanish-desk: [maria, jose]
    pacific: [tevita, sione]
  ```

  A change takes effect when Coglossa restarts. `coglossa doctor` reports a missing or invalid
  file, and warns about a team named like `admin_group`: it gives nobody anything, so list
  admins under `admins`.

Members of `admin_group` (default `coglossa-admins`) are admins; that group is not a team.
Names and team names compare without regard to the case of A–Z; other letters (`é`, `ß`) must
match as written. The proxy should send names in UTF-8; only ASCII spaces and tabs are trimmed
from a name's ends. Team names are 1–64 letters, digits, spaces, `.`, `_` or `-`.

When upgrading from a version before CGL-31: a personal set made by someone whose name has
letters outside A–Z was saved under the name as Coglossa then misread it ("JosÃ©" for "José").
`coglossa db upgrade` repairs those names (CGL-75) and says how many it changed, so each set
shows to its maker again. Rolling the database back does not undo the repair. Some names
cannot be repaired and are left as they are: names your proxy sent in latin-1 rather than
UTF-8, and names whose last letter was cut off when they were saved ("Pietà" saved as
"PietÃ"; this happens to some names ending in à, Å, х or م, and to some ending in a Chinese,
Japanese or Korean character). An admin still sees those sets and can move each one to one
of its maker's teams.

```yaml
auth:
  mode: trusted_header
  header: X-Remote-User
  groups_header: X-Remote-Groups   # only if the proxy always sets it
  admin_group: coglossa-admins
  team_groups_prefix: coglossa-    # optional: only these groups become teams (below)
  people_file: people.yaml
```

The nginx example above sets the groups header in both of its locations; keep it that way in
every location you add.

Only groups that a set can carry as its team become teams: 1–64 letters, digits, spaces, `.`,
`_` or `-`. Other groups (a path such as `/translators/es`, an LDAP name, accented letters) are
ignored, with a warning in the log; map them to plain names in your sign-in service, or list
the people in the people file instead.

#### Choosing which groups become teams

By default every group the proxy sends becomes a team, including broad directory groups such as
`Domain Users`. A set moved into such a team shows to almost everyone, and the team suggestions
fill with groups that mean nothing in Coglossa. Two settings narrow this:

- `team_groups_prefix`: only groups whose names start with it become teams, for example
  `coglossa-` for `coglossa-spanish-desk` and `coglossa-pacific`.
- `team_groups`: a list of the groups that become teams, for example
  `[spanish-desk, pacific]`.

With both set, a group becomes a team if it starts with the prefix **or** is on the list. With
neither, every group is a team, as before. Names compare the way team names always do: A–Z in
any case, other letters as written, ASCII spaces trimmed from the ends. An empty list
(`team_groups: []`) with no prefix means no group is a team.

The same rule applies to the people file's teams, so a team that does not match gives its
members nothing. The admin group is never a team, even when its name starts with the prefix
(`coglossa-admins` with the prefix `coglossa-`); its members are still admins. With a filter on,
an admin can make or move a set only into a team that matches, and never into the admin group;
Coglossa refuses any other team name.

Turning a filter on also hides sets already in a team that no longer matches: their members stop
seeing them, and only admins do. Move each such set into a matching team (**⋯ ▸ Move to
team…**). `coglossa doctor` shows the rule under `teams`, and warns when it leaves out teams the
people file names, or when no group can be a team at all (no prefix, and a `team_groups` list
that is empty or names only the admin group). Doctor cannot see the groups your proxy sends, so
sign in as a member of each team after turning the filter on and check that its sets still
show, or open `/api/me`, which lists your teams.

## 7. Running it

- **Status:** `docker compose ps`.
- **Logs:** `docker compose logs -f api worker`. Logs are JSON lines. Docker keeps up to 5 files of 50 MB per service.
- **Health check:** `docker compose exec api coglossa doctor`. The web app's Settings page shows the same checks under Technical details.
- **Stop:** `docker compose down` stops everything and keeps the data.
- **Start:** `docker compose up -d --wait`.
- After a reboot the services start by themselves once Docker starts. The worker waits up to 15 minutes for the scorer to load its models (section 3).

### How much reviewing a translation took

Admins see a **review summary** under **Technical details** at the bottom of a translation's
page: for the language shown, over the latest version of each file, how many passages were
checked and how many a reviewer changed, how much they changed on average (characters added,
removed or replaced, and as a share of the passage), and for each kind of flag how often it was
raised, marked "not a problem" (with the reasons given) and followed by a change to the passage.
That last figure says how often a flag points at something reviewers actually fix.

It is read from what reviewing already records; nothing is stored for it, and it changes no
setting or threshold. **Download every passage as a spreadsheet (CSV)** gives one row per passage
(its flags, whether it was checked and changed, the size of the change, and who reviewed it, by
the name the sign-in proxy passes), for the language or for one file. The CSV is UTF-8 with a
byte order mark, so Excel opens accents correctly, and a cell that would start a formula begins
with an apostrophe. The same figures are in the API (`GET /api/runs/{id}/review-summary`,
`GET /api/projects/{id}/review-summary`, and `.csv` after either), for admins only.

## 8. Backups

```sh
./backup.sh [DIR]
```

Run it in the install directory while Coglossa is running. `DIR` defaults to `./backups`. It writes two files, readable only by you:

- `coglossa-db-<stamp>.dump`: the database (projects, runs, translations, guides, reviews)
- `coglossa-files-<stamp>.tar.gz`: the uploaded documents

`<stamp>` is the UTC time, such as `20261012T020000Z`. The files appear under their final names only when both are complete, so a failed run leaves no file that looks like a backup.

The database and the files are captured a few seconds apart while Coglossa runs. A backup taken during an upload may miss that upload's file. Take backups at quiet times, or for a strictly consistent pair stop the writers first:

```sh
docker compose stop worker api
./backup.sh /var/backups/coglossa
docker compose start worker api
```

A daily backup at 02:00 that keeps 14 days (edit the paths; cron has no `PATH` for `docker` in some setups, so use full paths if needed):

```
0 2 * * * cd /opt/coglossa-<version> && ./backup.sh /var/backups/coglossa && find /var/backups/coglossa -name 'coglossa-*' -mtime +14 -delete
```

The backups contain every document: keep them private. Copy them off the host, and test a restore now and then.

### What a backup does not hold

A backup holds the database and the uploaded files, not the installation's own settings. Keep a copy of these beside your backups, and copy them again after every change:

- `config/coglossa.yaml`: your model servers, models and roles
- `config/tiers.yaml`, if you edited it
- `.env`: the database password, any backend API keys and your port settings. It holds secrets: store it where only administrators can read it, apart from the backups if you can.

Without them, a backup still restores (section 9, "On a new host"), but you must write the configuration again.

## 9. Restore

```sh
./restore.sh /var/backups/coglossa/coglossa-db-<stamp>.dump /var/backups/coglossa/coglossa-files-<stamp>.tar.gz
```

The database dump comes first, the files archive second. **This replaces all current data with the backup.** The script checks both files before it stops anything (it reads the whole dump, so a backup that was cut short is refused), asks you to type `restore`, stops the API and worker, restores, and starts everything again. Without a terminal (for example from cron) it needs `--yes`:

```sh
./restore.sh --yes DB_DUMP FILES_ARCHIVE
```

If a step fails after it has stopped Coglossa, the script restarts Coglossa and says what state the data is in; follow its message and run it again.

The database restore replaces the whole schema in one transaction, so a backup from an older version restores over a newer one (see "To go back" in section 10). If it fails, the database stays as it was.

### On a new host

To bring Coglossa back on another machine, for example after losing the old one:

1. Copy the bundle of the version that made the backup (or a newer one) and the backup files to the new host, check the bundle and unpack it (section 3, step 1).
2. Run `./install.sh` (with `--gpu` if the new host's scorer runs on a GPU).
3. Copy your saved `config/coglossa.yaml`, `config/tiers.yaml` (if you edited it) and `.env` into the new directory, over the files `install.sh` created.
4. Start it, restore the backup and check:

   ```sh
   docker compose up -d --wait
   ./restore.sh --yes DB_DUMP FILES_ARCHIVE
   docker compose exec api coglossa doctor
   ```

If you lost `.env`, keep the one `install.sh` created: the database dump holds no passwords or database roles, so it restores under a new database password too. Add your backend API keys to it again. If you also lost `config/coglossa.yaml`, write it again (section 3, step 3) before you start.

## 10. Upgrades

1. Back up: `./backup.sh`.
2. Extract the new bundle beside the old directory.
3. Copy `config/coglossa.yaml`, `.env` and, if you edited it, `config/tiers.yaml` from the old directory into the new one. `./install.sh` in step 5 keeps them.
4. In the old directory: `docker compose down`.
5. In the new directory:

   ```sh
   ./install.sh
   docker compose up -d --wait
   docker compose exec api coglossa doctor
   ```

   The `migrate` service upgrades the database before the API and worker start.

   The upgrade of the job queue to procrastinate 3.12 (CGL-106) includes four such migrations, revisions 0009 to 0012. The last one removes the database functions that older versions call to queue a job, so **the old API and worker must be stopped before `migrate` runs**: step 4 does that. Do not skip it, and do not start the new version while the old one still runs; an old worker that is still running when the migration ends cannot queue the next step of a translation, and that translation stops.

   Revision 0011 builds an index on the job queue without blocking it; with a long job history this can take a few minutes. If `migrate` stops while it runs (for example the host restarts), run `docker compose up -d --wait` again: the migration drops the unfinished index and builds it again.

The data lives in Docker volumes that belong to the project name `coglossa`, not to the directory, so the new directory finds it.

If a cron job runs `backup.sh`, change its `cd` to the new directory (section 8). Once the new version runs, you can delete the old directory.

To go back: migrations only go forward. Stop the new version (`docker compose down` in the new directory), then in the old directory restore the backup you took in step 1: `./restore.sh DB_DUMP FILES_ARCHIVE`. It replaces the newer database schema with the backup's and starts the old version. Anything done after the backup is lost.

## 11. Troubleshooting

| Symptom | Run | Fix |
| --- | --- | --- |
| `up` says `service "migrate" didn't complete successfully`, or `api` keeps restarting | `docker compose logs migrate api` | The config is invalid; the log says which setting. Fix `config/coglossa.yaml` (or `config/tiers.yaml`), then apply it (section 5, "Applying changes"). |
| `doctor` fails on a backend | `docker compose exec api coglossa doctor` | Unreachable: check `base_url`, the port and the firewall from the host (`curl http://<server>/v1/models`). Wrong model id: use an id from that list. No structured output: set the model's `structured_output` to what the server supports, or `none`. |
| `doctor` warns about `leftover files` | `docker compose exec api coglossa doctor`, `docker compose logs api` | Deleting a set removed it, but not its files (the api log has an error naming the folder and why). `doctor` lists such folders and never deletes them. Once you have checked a listed folder is not needed, remove it by hand: `docker compose exec api rm -r /data/files/projects/<id>`, or with the provider's tools for a bucket. A set being added or deleted at that moment can show briefly; run `doctor` again. |
| The scorer stays `starting` or becomes unhealthy | `docker compose logs scorer` | Missing `models/` files: copy the bundle again and run `./install.sh`. Out of memory: MetricX large needs 16 GB, XL on CPU 32 GB; set `COGLOSSA_QE_MODEL=metricx-24-hybrid-large-v2p6` in `.env`. Still loading after 15 minutes (the log shows no error): set `COGLOSSA_SCORER_START_PERIOD=30m` in `.env` (section 3). |
| Runs finish with no scores ("unscored") | `docker compose ps`, `docker compose logs scorer worker` | The scorer was down or unreachable, or no scorer or judge is configured. Fix the scorer until `docker compose ps` shows it healthy, then open the translation, then choose **Translate again** from its ⋯ menu; a finished run cannot be retried. |
| The GPU is not visible to the scorer | `docker run --rm --gpus all ubuntu nvidia-smi` | If this fails, install the NVIDIA Container Toolkit, restart Docker, and check the driver is 525 or newer. |
| Everyone sees "You're signed out. Reload the page to sign in again." and reloading does not help | Open `https://<your address>/api/me` in a browser, through the proxy | The proxy is not passing the user name, or the proxy secret, to Coglossa. The answer names which: `missing X-Remote-User header` means the header line is missing from that `location` (or the sign-in service sent no name); "only answers requests from the Coglossa sign-in proxy" means `X-Coglossa-Proxy-Secret` is missing or differs from `.env` (section 6). |
| Port 8080 is taken | `ss -ltn 'sport = :8080'` | Set `COGLOSSA_HTTP_PORT=<other>` in `.env`, then apply it (section 5, "Applying changes"). |
| Permission errors on `config/` | `docker compose logs migrate` | The containers run as uid 10001. Make the files readable: `chmod -R a+rX config`. |
| `install.sh` reports a damaged bundle | `sha256sum -c SHA256SUMS` | A file is missing or changed. Copy the bundle again (and check its `.sha256` file). |
| Compose says "run ./install.sh first" | `ls -a` | `.env` is missing. Run `./install.sh` in this directory. If this is an upgrade, copy the old `.env`; do not create a new password. |

## 12. Benchmark your models

`coglossa bench` measures how well your models translate, and how well Coglossa's flags catch errors, before you rely on them. It runs offline in the worker container, on the FLORES-200 test sentences shipped in `bench/flores200/`. FLORES-200 is CC BY-SA 4.0; its attribution is in `bench/flores200/ATTRIBUTION.txt`. A bundle built without FLORES-200 has no `bench/flores200/`, and bench says it found no data there.

It measures, per system and language pair:

- **MetricX-24 in reference mode**: the translation compared with FLORES's human reference, an error score from 0 to 25 (lower is better). It needs the scorer. A sentence too long for MetricX has no score; it is never cut short.
- **chrF**: character overlap with the reference, from 0 to 100 (higher is better).
- **Tokens and time**: what the translations cost.
- **Flag recall and false positives** (`bench flags`): known errors are written into the reference translations, and Coglossa's own scoring decides which to flag. Recall is the share of errors caught, per kind of error and per language tier. The clean false-positive rate is the share of correct (clean) reference translations flagged anyway: it is the number that carries over to real use. Precision, caught errors over caught errors plus flagged clean sentences, is shown too, but it reflects this bench's mix of damaged and clean sentences (several damaged per clean one); real translations have far fewer errors, so real precision is lower.

A system is either your full Coglossa configuration (`pipeline`) or a single model translating in one pass (`baseline`), which shows what the agents add.

### Run it

```sh
cp config/bench.example.yaml config/bench.yaml   # edit pairs and systems
mkdir -p bench-results
docker compose run --rm -T -v "$PWD/bench-results:/out" --user "$(id -u):$(id -g)" \
  worker coglossa bench run /etc/coglossa/bench.yaml --out /out
```

In `config/bench.yaml`, choose the language `pairs` (BCP-47 tags such as `en>es` or `en>zh-Hans`), the number of `sentences` per pair (100 is a good start), and the `systems`. Set the baseline's `model` to a model key from `config/coglossa.yaml`. Inside the container, `config/` is `/etc/coglossa`, so a system's `config` is a path there.

The `--user` flag runs bench as you, so the results in `bench-results/` belong to you and not to the container's user. If your umask is strict, make the new file readable first: `chmod a+r config/bench.yaml`.

On real models a run takes hours: every system translates every sentence of every pair. It uses the same model servers as your users' translations, so run it at a quiet time. Bench first checks every system's configuration (and a baseline's model) and stops with one line naming the system if one is wrong. You can stop it (Ctrl-C) and resume it by running the same command again: each system and pair is saved as soon as it finishes (`bench-results/runs/<system>/<pair>.json`) and is not redone. `--force` after `--out /out` redoes them all. A pair that fails (a model server timing out, for example) is recorded with its error and redone by the next run, while the others continue. If you change the sentences, seed or split, or a pair's data, the pairs they affect are run again ("settings changed" in the log). So are they if you change a model, its backend's URL, the roles, chunking or profiles, or the text of a prompt template. A baseline is run again only if its own model, or anything that changes its chunk size, changes. Thresholds, tiers, timeouts, streaming, concurrency, retry, audit and the scorer do not run anything again. Results written by an earlier version of Coglossa are run again too ("written by an older version"); results of systems or pairs you removed stay in `bench-results/runs/` but are left out of the summary. If the scorer was down, or has no MetricX model loaded, the pairs are saved without MetricX, and the next run with a working scorer scores them from the saved translations without translating again. The same happens when you switch the scorer to another MetricX model.

The results are in `bench-results/summary.md`, one row per system and pair: MetricX (lower is better), how many units MetricX scored out of all, how many were too long for MetricX (because of the source, or because of the system's output), chrF (higher is better), tokens in and out, seconds, and the errors met. So that systems compare, a pair's MetricX is the mean over the sentences every system of the pair has a score for. `summary.json` holds the same table for scripts, with each system's own MetricX mean as well, and `bench-meta.json` the dataset settings and the systems.

### Test the flags, then calibrate

```sh
docker compose run --rm -T -v "$PWD/bench-results:/out" --user "$(id -u):$(id -g)" \
  worker coglossa bench flags /etc/coglossa/bench.yaml --scoring-config /etc/coglossa/coglossa.yaml --out /out
docker compose run --rm -T -v "$PWD/bench-results:/out" --user "$(id -u):$(id -g)" \
  worker coglossa bench calibrate --out /out --scoring-config /etc/coglossa/coglossa.yaml
```

`bench flags` writes omissions, changed numbers, swapped names and ignored locked terms into the reference translations. With an `injector` in `config/bench.yaml` (a model that writes errors on request) it also tests negation and register shifts. It then scores the clean and the damaged references with the judge and scorer of `--scoring-config`, your live configuration, every sentence of a passage at once, as a real translation is scored. `bench-results/flag-summary.md` has one table per language tier: for each kind of error, how many were caught out of how many (recall), how many clean sentences were flagged anyway (the clean false-positive rate for all flags together), the precision, and how many sentences lost a signal because a judge, back-translation or scorer call failed. The model's negations and register shifts are not checked, and some may not be errors at all, so their recall is a lower bound; the end of `flag-summary.md` shows three examples of each kind of error, the reference and its damaged version, so you can check them by eye.

`bench flags` takes about 40 to 60 minutes a language pair with a CPU scorer. Add `--pairs 3` to score three pairs at the same time (default one); each pair's results are saved in `bench-results/flags/` as soon as it finishes, and a later run keeps every pair whose settings have not changed, so after a crash or a stop, run the same command again and only the unfinished pairs are scored. Changing the judge, the models, the prompts, the thresholds, the language tiers or a server's address scores a pair again; timeouts, concurrency and retries do not. `--force` scores every pair again: use it after loading another MetricX model into the scorer, which the saved results cannot tell, and after raising a timeout when the log says a kept pair's sentences lost a signal. Use `--pairs` rather than several bench processes: two processes writing to the same `bench-results` would score the same pairs twice. The pairs share one scorer, and on a CPU its requests queue behind each other: raise `timeout_s` under `scorer:` in the scoring config (900 s, say) before you start, since the default 300 s can run out while a request waits.

`bench calibrate` turns both results into suggestions, and explains each one in the file's comments (it also prints them):

- `bench-results/calibration.yaml` suggests the `meaning` threshold per tier and the `style` threshold. Each is the highest threshold that flags at most 10% of the clean sentences with its own flag (`low_meaning` or `style_drift`, plus critical errors found by the judge); `--max-false-positive-rate 0.05` asks for at most 5%. Other flags, such as terminology and omission, also fire on some clean sentences, so the rate for all flags together, in `flag-summary.md`, is higher. For each, the comments give the clean false-positive rate it reaches and the recall, per kind of error and averaged over the kinds. Each kind shows how many errors were caught out of how many, for example `omission 60% (18/30)`. A kind with fewer than 10 examples says so and is left out of the average; if every kind has fewer, no average is given. A threshold stays as it is, and the comment says why, when its tier has too few examples, when nothing could be scored, when critical errors found by the judge already flag more clean sentences than allowed, or when the threshold would catch none of the errors. Sentences that lost a signal to a failed call are left out.
- `bench-results/tiers.calibrated.yaml` is your current tiers file with each measured pair added under `pairs`, in tier A, B or C by the mean MetricX of the first pipeline system in `config/bench.yaml` (`--tier-system NAME` picks another). The cut-offs are `tiers` in `config/bench.yaml` (A up to 5.0, B up to 10.0 by default).

The thresholds were measured with each pair in its current tier, so a new tier changes which threshold applies to it. If you take the new tiers, apply them first, then run `bench flags` and `bench calibrate` again and use the thresholds from that second round.

To apply them:

1. Copy `tiers.calibrated.yaml` to `config/tiers.yaml`, and make sure `config/coglossa.yaml` has `language_tiers_file: tiers.yaml` under `scoring:` (section 5, "Language tiers"). If you changed the tiers, run `bench flags` and `bench calibrate` again now.
2. Paste the `scoring:` section of `calibration.yaml` into `config/coglossa.yaml`. If the file already has a `scoring:` section, put the `thresholds:` under it rather than adding a second one.
3. Apply the change:

   ```sh
   docker compose up -d --wait --force-recreate migrate api worker
   docker compose exec api coglossa doctor
   ```

### Compare two configurations

To test a change before you make it live (another model as the judge, say), put the candidate in its own file, for example `config/coglossa.candidate.yaml`, and add a second `pipeline` system for it:

```yaml
systems:
  - { name: current, kind: pipeline, config: /etc/coglossa/coglossa.yaml }
  - { name: candidate, kind: pipeline, config: /etc/coglossa/coglossa.candidate.yaml }
```

Bench never touches the database, so the candidate does not affect your users' runs. Both systems translate the same sentences, and `summary.md` shows them side by side.

Numbers do not tell the whole story, so let people judge too. This writes a blind A/B sheet:

```sh
docker compose run --rm -T -v "$PWD/bench-results:/out" --user "$(id -u):$(id -g)" \
  worker coglossa bench ab --out /out --a current --b candidate
```

`bench-results/ab-sheet.csv` shows each source sentence with the two translations in random order, and does not say which system wrote which. Sentences both systems translated alike are left out. Give it to your reviewers; they mark the translation they prefer (1, 2 or same). `ab-key.csv` says which system wrote which: keep it from the reviewers until they are done. The sheet quotes FLORES-200 (CC BY-SA 4.0), so share `ab-sheet.csv` together with `LICENSE.txt` and `ATTRIBUTION.txt` from the same folder. `--pair 'en>es'` (repeatable, quoted for the shell) limits the sheet to some pairs.

`config/bench.yaml` and `bench-results/` are not in the backups (section 8). Copy them along with your configuration when you upgrade (section 10).

## 13. Uninstall

```sh
docker compose down -v
```

**This deletes all data: the database and the uploaded files.** Back up first if you may need them.

Then remove the images:

```sh
docker image rm coglossa/app:<version> coglossa/scorer:<version>-cuda postgres:16-alpine
```

With the CPU scorer (`"build": "cpu"` in `manifest.json`), the scorer image is `coglossa/scorer:<version>`, without `-cuda`.

Then delete the install directory.

## 14. What is inside

- The files in `sbom/` are CycloneDX software bills of materials: one for each of the three images and one for the web app's packages.
- `manifest.json` records each image's id and the Hugging Face revisions of the weights.
- The weights' licences: MetricX-24, mT5 and LaBSE are Apache-2.0.
- FLORES-200 (`bench/flores200/`) is CC BY-SA 4.0, by the NLLB Team (Meta AI); its attribution is in `bench/flores200/ATTRIBUTION.txt`. It is shipped unmodified.
- Coglossa sends nothing outside the host except requests to the model servers you configure.
