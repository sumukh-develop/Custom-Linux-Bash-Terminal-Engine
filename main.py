from __future__ import annotations

import asyncio
import logging
import os
import shlex
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from execution_engine import CommandExecutionEngine
from session_manager import SessionManager, TerminalSession


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "WARNING").upper(),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("terminal-engine")

# Long-running session support (120 minutes)
HEARTBEAT_INTERVAL_SECONDS = 30  # Send ping every 30 seconds
HEARTBEAT_TIMEOUT_SECONDS = 300  # 5 minutes timeout for heartbeat response
SESSION_CLEANUP_INTERVAL_SECONDS = 600  # Check for idle sessions every 10 minutes

app = FastAPI(title="Terminal Engine Service")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
engine = CommandExecutionEngine(required_paths=["/home/user/project"])
# Session manager with 120-minute idle timeout (7200 seconds)
sessions = SessionManager(idle_ttl_seconds=7200)
BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"


@app.on_event("startup")
async def start_session_cleanup() -> None:
    asyncio.create_task(cleanup_idle_sessions())


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "active_sessions": len(sessions.sessions)})


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.get("/")
async def index() -> Response:
    if not FRONTEND_DIST.exists():
        return HTMLResponse(
            """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Terminal Engine</title>
    <style>
      body {
        margin: 0;
        font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
        background: #061012;
        color: #d7fbe5;
        min-height: 100vh;
        display: grid;
        place-items: center;
        padding: 24px;
      }
      main {
        max-width: 720px;
        border: 1px solid rgba(124, 255, 170, 0.18);
        border-radius: 16px;
        background: rgba(6, 12, 12, 0.9);
        padding: 24px;
        box-shadow: 0 24px 90px rgba(0, 0, 0, 0.45);
      }
      code {
        color: #7cffaa;
      }
    </style>
  </head>
  <body>
    <main>
      <h1>Terminal frontend not built yet</h1>
      <p>Run the frontend build, or start the Docker image to get the bundled UI.</p>
      <p><code>cd frontend && npm install && npm run build</code></p>
    </main>
  </body>
</html>"""
        )
    return FileResponse(FRONTEND_DIST / "index.html")


async def cleanup_idle_sessions() -> None:
    while True:
        await asyncio.sleep(SESSION_CLEANUP_INTERVAL_SECONDS)
        removed = sessions.cleanup_idle()
        if removed:
            logger.debug("Cleaned up %s idle terminal sessions", removed)


def format_response(session: Any, result: dict[str, Any]) -> dict[str, Any]:
    # Check if this is a special nano response
    if result.get("type") == "nano":
        return {
            "type": "nano",
            "data": result.get("data", {}),
        }
    
    output = result.get("output") or ""
    error = result.get("error") or ""
    combined_output = "\n".join(part for part in (output, error) if part)

    return {
        "type": "response",
        "data": {
            "output": combined_output,
            "prompt": sessions.generate_prompt(session),
        },
    }


def build_terminal_history(session: Any) -> str:
    lines: list[str] = []
    for entry in session.history:
        lines.append(f"{entry['prompt']}{entry['command']}")
        if entry["output"]:
            lines.append(entry["output"])
    return "\n".join(lines)


async def send_error_response(session: Any, websocket: WebSocket, output: str) -> None:
    await websocket.send_json(
        {
            "type": "response",
            "data": {
                "output": output,
                "prompt": sessions.generate_prompt(session),
            },
        }
    )


async def heartbeat(
    websocket: WebSocket,
    session: TerminalSession,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
            break
        except asyncio.TimeoutError:
            try:
                await websocket.send_json({"type": "ping"})
                if time.monotonic() - session.last_seen > HEARTBEAT_TIMEOUT_SECONDS:
                    await websocket.close(code=1000, reason="Heartbeat timeout")
                    stop_event.set()
                    break
            except Exception:
                logger.debug("Heartbeat failed for session_id=%s", session.session_id)
                stop_event.set()
                break


@app.websocket("/terminal")
async def terminal(websocket: WebSocket) -> None:
    logger.debug("WebSocket handler triggered")
    origin = websocket.headers.get("origin", "")
    requested_session_id = websocket.query_params.get("session_id")
    await websocket.accept()
    session = sessions.get_or_create(requested_session_id)
    logger.debug("WebSocket accepted for session_id=%s origin=%s", session.session_id, origin)

    await websocket.send_json(
        {
            "type": "init",
            "data": {
                "session_id": session.session_id,
                "prompt": sessions.generate_prompt(session),
            },
        }
    )

    stop_heartbeat = asyncio.Event()
    heartbeat_task = asyncio.create_task(heartbeat(websocket, session, stop_heartbeat))

    try:
        while True:
            try:
                message = await websocket.receive_json()
            except WebSocketDisconnect:
                raise
            except Exception as exc:
                logger.warning(
                    "Invalid JSON for session_id=%s error=%s",
                    session.session_id,
                    str(exc),
                )
                await send_error_response(session, websocket, "Invalid input format")
                continue

            sessions.touch(session)
            if not isinstance(message, dict):
                logger.warning(
                    "Invalid message payload type for session_id=%s payload_type=%s",
                    session.session_id,
                    type(message).__name__,
                )
                await send_error_response(session, websocket, "Invalid input format")
                continue

            logger.debug("Received: %s", message)
            if "type" not in message:
                logger.warning("Missing message type for session_id=%s", session.session_id)
                await send_error_response(session, websocket, "Missing message type")
                continue

            message_type = message.get("type")

            if message_type == "pong":
                continue

            if message_type == "command":
                command = str(message.get("data", ""))
                prompt_before = sessions.generate_prompt(session)
                result = engine.execute(session, command)
                output = result.get("output") or ""
                error = result.get("error") or ""
                visible_output = "\n".join(part for part in (output, error) if part)
                session.history.append(
                    {
                        "prompt": prompt_before,
                        "command": command,
                        "output": visible_output,
                    }
                )

                logger.debug("Sending command response")
                await websocket.send_json(format_response(session, result))
                continue

            if message_type == "mode":
                mode = str(message.get("data", "")).lower()
                if mode in {"exam", "learning"}:
                    session.mode = mode
                    result = {
                        "output": f"Mode set to {mode}" if mode == "learning" else "",
                        "error": "",
                        "message": f"Mode set to {mode}",
                        "status": "success",
                        "exit_code": 0,
                        "duration_ms": 0,
                    }
                else:
                    result = {
                        "output": "",
                        "error": "mode: expected exam or learning",
                        "message": "",
                        "status": "error",
                        "exit_code": 1,
                        "duration_ms": 0,
                    }
                logger.debug("Sending mode response")
                await websocket.send_json(format_response(session, result))
                continue

            if message_type == "submit":
                session.result = engine.evaluate(session)
                history_output = build_terminal_history(session)
                logger.debug("Sending submit response")
                await websocket.send_json(
                    {
                        "type": "response",
                        "data": {
                            "output": history_output,
                            "prompt": sessions.generate_prompt(session),
                        },
                    }
                )
                continue

            if message_type == "nano_save":
                data = message.get("data", {})
                filename = data.get("file")
                content = data.get("content", "")
                
                if not filename:
                    await send_error_response(session, websocket, "nano: missing filename")
                    continue
                
                # Save the file using echo redirection
                save_command = f"echo {shlex.quote(content)} > {shlex.quote(filename)}"
                result = engine.execute(session, save_command)
                
                # Send confirmation
                if result.get("exit_code") == 0:
                    await websocket.send_json(
                        {
                            "type": "response",
                            "data": {
                                "output": f"File {filename} saved",
                                "prompt": sessions.generate_prompt(session),
                            },
                        }
                    )
                else:
                    await send_error_response(
                        session, websocket, result.get("error", "Failed to save file")
                    )
                continue

            await send_error_response(
                session, websocket, f"Unsupported message type: {message_type}"
            )

    except WebSocketDisconnect:
        logger.debug("Client disconnected session_id=%s", session.session_id)
    except Exception as exc:
        logger.exception("Unhandled error in session_id=%s error=%s", session.session_id, str(exc))
        try:
            await send_error_response(session, websocket, "Internal server error")
        except Exception:
            logger.debug(
                "Failed to send internal error response for session_id=%s",
                session.session_id,
            )
        try:
            await websocket.close(code=1011, reason="Internal server error")
        except Exception:
            logger.debug("WebSocket already closed for session_id=%s", session.session_id)
    finally:
        sessions.touch(session)
        stop_heartbeat.set()
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=4041,
        ws_ping_interval=30,  # Send WebSocket ping every 30 seconds
        ws_ping_timeout=300,  # 5 minutes timeout for ping response
        timeout_keep_alive=300,  # Keep connection alive for 5 minutes
    )
