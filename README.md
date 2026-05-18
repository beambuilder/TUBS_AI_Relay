# TUBS AI Relay

An Umbrel Community App that exposes the **TU Braunschweig KI-Toolbox** as an
**OpenAI-compatible** API for other Umbrel apps (e.g. [openclaw/Hermes][hermes]),
plus a simple built-in chat UI to test the connection.

The relay translates incoming `POST /v1/chat/completions` requests into the
TUBS KI-Toolbox `/api/v1/chat/send` (cloud) or `/api/v1/localChat/send`
(on-premise) protocol, including server-sent-event streaming.

```
[ Hermes / OpenClaw / curl ] --(OpenAI API)--> [ TUBS AI Relay ] --(TUBS API)--> [ ki-toolbox.tu-braunschweig.de ]
```

[hermes]: https://github.com/openclaw/hermes

## Repo layout

```
.
├── umbrel-app-store.yml          # community app store manifest
├── tubs-ai-relay/                # the actual Umbrel app
│   ├── umbrel-app.yml            # app listing for the Umbrel UI
│   ├── docker-compose.yml        # services for the app
│   └── app/                      # source for the docker image
│       ├── Dockerfile
│       ├── requirements.txt
│       ├── main.py               # FastAPI app: OpenAI-compatible proxy + UI
│       └── static/               # HTML / CSS / JS chat UI
└── .github/workflows/docker.yml  # builds & pushes image to GHCR
```

## How it works

* `POST /v1/chat/completions` — OpenAI-compatible chat completions (streaming
  via SSE and non-streaming). The relay flattens the OpenAI `messages` array
  into one TUBS prompt and routes `system` messages into TUBS
  `customInstructions`. A fresh TUBS thread is created on every call; clients
  remain responsible for their own conversation history (just like a real
  OpenAI deployment).
* `GET /v1/models` — lists all supported TUBS cloud + on-premise models.
* `GET /` — built-in chat UI.
* `GET /healthz` — health probe / quick config check.

## Supported models

Pulled from `python-tubskitb` (kept in sync with the official client):

**Cloud:** `gpt-5.1`, `gpt-5`, `gpt-4.1`, `gpt-4o`, `gpt-4o-mini`, `gpt-4-turbo`,
`gpt-4`, `gpt-3.5-turbo-0125`, `o4-mini`, `o3`, `o3-mini`, `o1`.

**On-premise:** `openai/gpt-oss-120b`, `Qwen/Qwen3-30B-A3B`,
`Qwen/Qwen2.5-Coder-32B-Instruct`, `mistralai/Mistral-Small-24B-Instruct-2501`,
`microsoft/phi-4`.

The relay automatically picks the correct upstream base URL (cloud vs. local)
based on the chosen model.

---

## Deploying on Umbrel

### 1. Build & publish the image

The compose file references `ghcr.io/<your-github-user>/tubs-ai-relay:0.1.0`.
Pick one of the two routes:

#### A. GitHub Actions (recommended)

1. Push this repo to your own GitHub account.
2. The workflow at `.github/workflows/docker.yml` builds and pushes a
   `linux/amd64` + `linux/arm64` image to **GHCR** on every push to
   `master`/`main` (and on releases).
3. Make the package public:
   `github.com/<you>?tab=packages` → *tubs-ai-relay* → *Package settings* →
   *Change visibility* → *Public*.
4. Edit `tubs-ai-relay/docker-compose.yml` and replace
   `ghcr.io/niclasprzibylla/tubs-ai-relay:0.1.0` with
   `ghcr.io/<your-user>/tubs-ai-relay:0.1.0` (or `:latest`).

#### B. Build directly on the Umbrel host

SSH into your Umbrel and run:

```bash
git clone https://github.com/<your-user>/TUBS_AI_Relay.git ~/tubs-ai-relay-src
cd ~/tubs-ai-relay-src/tubs-ai-relay/app
sudo docker build -t ghcr.io/niclasprzibylla/tubs-ai-relay:0.1.0 .
```

Now Docker has the image locally under the tag the compose file expects, so
the next install will skip the pull.

### 2. Add the Community App Store to Umbrel

In the Umbrel web UI:

1. *App Store* → *Community App Stores* → *Add* and paste the GitHub URL of
   this repo (e.g. `https://github.com/<your-user>/TUBS_AI_Relay`).
2. The new store "**TU Braunschweig**" shows up; you'll see **TUBS AI Relay**
   inside.
3. Click *Install*.

### 3. Provide your TUBS API key

The relay needs your personal token from
<https://ki-toolbox.tu-braunschweig.de>. Two ways:

#### Option A — env var in `docker-compose.yml`

SSH into Umbrel and edit
`~/umbrel/app-data/tubs-ai-relay/docker-compose.yml`:

```yaml
    environment:
      TUBS_API_KEY: paste-your-token-here
```

Then restart the app from the Umbrel UI (or `umbreld client apps.restart --appId tubs-ai-relay`).

#### Option B — secret file (preferred)

```bash
mkdir -p ~/umbrel/app-data/tubs-ai-relay/data
echo 'paste-your-token-here' > ~/umbrel/app-data/tubs-ai-relay/data/tubs_api_key
chmod 600 ~/umbrel/app-data/tubs-ai-relay/data/tubs_api_key
```

That directory is bind-mounted into the container read-only at `/data`. The
relay reads `/data/tubs_api_key` whenever `TUBS_API_KEY` is empty. Restart the
app for the change to take effect.

### 4. Test the UI

Open the app from Umbrel's home screen, pick a model in the sidebar
(`gpt-4o-mini` is a good cheap default), start a new chat, send a message and
verify a streaming response.

The status line in the sidebar shows whether `TUBS_API_KEY` is configured.

---

## Connecting Hermes / OpenClaw

Inside the Umbrel docker network the relay is reachable at:

```
http://tubs-ai-relay_server_1:8000
```

Set Hermes / OpenClaw to use this as the **OpenAI base URL**:

| Setting          | Value                                              |
|------------------|----------------------------------------------------|
| OpenAI base URL  | `http://tubs-ai-relay_server_1:8000/v1`            |
| OpenAI API key   | anything (or your `RELAY_API_KEY` if you set one)  |
| Model            | e.g. `gpt-4o`, `gpt-5`, `Qwen/Qwen3-30B-A3B`       |

If you set `RELAY_API_KEY` in `docker-compose.yml`, the relay enforces
`Authorization: Bearer <RELAY_API_KEY>` on every API call. Leave it empty for
an open relay inside the Umbrel network.

### Smoke test from another container

```bash
curl -N http://tubs-ai-relay_server_1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "gpt-4o-mini",
    "stream": true,
    "messages": [{"role": "user", "content": "Say hi."}]
  }'
```

---

## Local development

```bash
cd tubs-ai-relay/app
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export TUBS_API_KEY=...    # your TUBS token
uvicorn main:app --reload --port 8000
```

Then open <http://localhost:8000>.

## Environment variables

| Variable             | Default              | Description                                                                 |
|----------------------|----------------------|-----------------------------------------------------------------------------|
| `TUBS_API_KEY`       | _(empty)_            | TUBS KI-Toolbox API token. Required.                                        |
| `TUBS_API_KEY_FILE`  | `/data/tubs_api_key` | Fallback path for the token if `TUBS_API_KEY` is empty.                     |
| `TUBS_DEFAULT_MODEL` | `gpt-4o-mini`        | Used when the client does not send a `model` field.                         |
| `RELAY_API_KEY`      | _(empty)_            | Optional shared secret. If set, callers must send `Authorization: Bearer ...`. |

## Credits

* TU Braunschweig — KI-Toolbox API.
* `python-tubskitb` reference client (`https://git.rz.tu-bs.de/ias/python-tubskitb`).
* Built on top of the official Umbrel Community App Store template.
