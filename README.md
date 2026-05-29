# Production Terminal Engine

FastAPI + WebSockets terminal simulator with a fully in-memory bash-like
environment. No command is ever executed on the host OS.

## Files

```text
main.py
execution_engine.py
session_manager.py
Dockerfile
docker-compose.yml
frontend/
gunicorn_conf.py
uvicorn_worker.py
nginx-terminal.conf
requirements.txt
README.md
```

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
uvicorn main:app --host 127.0.0.1 --port 4041 --ws-ping-interval 20 --ws-ping-timeout 120
```

Production Docker runs Gunicorn with Uvicorn workers:

```bash
docker compose up --build
```

The terminal UI is served at:

```text
http://localhost:4041
```

The WebSocket client connects to:

```text
ws://localhost:4041/terminal
```

To reconnect to an existing in-memory session, pass the previous `session_id`:

```text
ws://localhost:4041/terminal?session_id=<session-id>
```

## Backend

- FastAPI WebSocket endpoint: `/terminal`
- One isolated session per `session_id`
- Session fields:
  - `cwd`
  - virtual file system
  - `history`
  - `env`
  - `processes`
  - `mode`
- Virtual root starts at `/home/user`
- Dict-backed filesystem
- Session is preserved across reconnects
- Idle sessions are cleaned up after 2 hours
- Server sends `{ "type": "ping" }` every 20 seconds
- Client should reply with `{ "type": "pong" }`

## Commands

Supported simulated bash commands:

```text
pwd
ls
cd
mkdir
touch
rm
echo
cat
grep (with -c flag for counting)
wc
head
tail
sort
uniq
cut
tr
chmod
find
ps
kill
env
export
ping
mode
cp
mv
nano (simulated editor)
sudo (privilege wrapper)
```

### 🆕 New Features

**grep -c** - Count matching lines instead of displaying them
```bash
grep -c "ERROR" logfile.txt  # Returns: 5
```

**nano** - Simulated text editor that returns file content for frontend editing
```bash
nano config.txt  # Opens editor modal in frontend
```

**sudo** - Privilege escalation wrapper (simulated, no actual privileges)
```bash
sudo mkdir /admin  # Executes with [sudo] indicator
```

See [NEW_FEATURES.md](NEW_FEATURES.md) for complete documentation.

Also supported:

- Relative and absolute paths
- `..` and `~`
- Chaining with `&&`
- Pipelines with `|`
- `echo text > file`
- `echo text >> file`
- Output redirection with `>` and `>>`
- Bash-style errors such as `No such file or directory`

## WebSocket Protocol

Client command:

```json
{ "type": "command", "data": "mkdir test" }
```

Client submit:

```json
{ "type": "submit" }
```

Client nano save (after editing):

```json
{
  "type": "nano_save",
  "data": {
    "file": "config.txt",
    "content": "updated file content"
  }
}
```

Server init:

```json
{
  "type": "init",
  "data": {
    "session_id": "session-id",
    "prompt": "user@server:~$ "
  }
}
```

Server nano response:

```json
{
  "type": "nano",
  "data": {
    "filename": "config.txt",
    "path": "/home/user/config.txt",
    "content": "existing file content"
  }
}
```

Server heartbeat:

```json
{ "type": "ping" }
```

Client heartbeat response:

```json
{ "type": "pong" }
```

## Production Notes

- Default Docker log level is `WARNING`.
- `WEB_CONCURRENCY` controls Gunicorn workers. The default formula is `(2 x CPU cores) + 1`; `docker-compose.yml` pins it to `4`.
- `timeout` is `7200` (120 minutes), `keepalive` is `300` (5 minutes), and WebSocket ping timeout is `300` (5 minutes).
- **Long-running session support**: Sessions stay alive for up to 120 minutes with automatic heartbeat every 30 seconds.
- Use `nginx-terminal.conf` when deploying behind Nginx so idle WebSockets are not closed early.
- If you run multiple containers, add sticky sessions at the load balancer or move sessions to Redis.

### Long-Running Sessions

The engine is optimized for long-running sessions (up to 120 minutes):

- **Automatic heartbeat**: Server sends ping every 30 seconds
- **Session timeout**: 7200 seconds (120 minutes) of inactivity
- **Graceful cleanup**: Expired sessions cleaned up every 10 minutes
- **Client requirement**: Must respond to `{"type":"ping"}` with `{"type":"pong"}`

See [LONG_RUNNING_SESSIONS.md](LONG_RUNNING_SESSIONS.md) for detailed configuration.

Server command response:

```json
{
  "type": "response",
  "data": {
    "output": "file.txt",
    "prompt": "user@server:~/test$ "
  }
}
```

Server submit response:

```json
{ "type": "response", "data": { "output": "user@server:~$ pwd", "prompt": "user@server:~$ " } }
```

## Frontend

The browser UI is a TypeScript terminal app in `frontend/`.

To run it locally:

```bash
cd frontend
npm install
npm run dev
```

If the frontend runs on Vite's default port, set the websocket URL to:

```text
ws://localhost:4041/terminal
```

## Evaluation

The default evaluation checks whether this path exists:

```text
/home/user/project
```

Try:

```bash
mkdir project
\submit
```
