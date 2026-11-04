from __future__ import annotations

import json
import base64
import hashlib
import os
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from datetime import datetime
from naming import DEFAULT_FILE_TEMPLATE, render_name, read_metadata, write_metadata, metadata_path, delete_metadata
from pathlib import Path
from contextlib import closing


APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
DATA_FILE = APP_DIR / "fpvsim_clipper_data.json"
TIMING_VERSION = 2


def hidden_process_options() -> dict:
    """Prevent helper programs from flashing a console on Windows."""
    if platform.system() == "Windows":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def low_priority_process_options() -> dict:
    """Run optional media work without competing aggressively with VelociDrone."""
    if platform.system() == "Windows":
        return {
            "creationflags": subprocess.CREATE_NO_WINDOW
            | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x00004000)
        }
    return {}


def detect_velocidrone_ip(port: int = 60003) -> str:
    """Find the local interface on which VelociDrone is listening."""
    candidates = []
    try:
        candidates.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80)); candidates.append(probe.getsockname()[0]); probe.close()
    except OSError:
        pass
    candidates.extend(["127.0.0.1", "localhost"])
    for host in dict.fromkeys(candidates):
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return host
        except OSError:
            continue
    return next((host for host in candidates if host not in ("127.0.0.1", "localhost")), "127.0.0.1")


def normalize_course_name(value: str) -> str:
    return re.sub(r"_+", "_", value.replace("+", "_")).strip(" _")


def detect_course_name(log_path: str = "") -> str | None:
    """Best-effort current-track discovery from VelociDrone log/settings data."""
    if log_path:
        try:
            lines = Path(log_path).read_text(encoding="utf-8", errors="ignore").splitlines()[-8000:]
            patterns = [
                re.compile(r"(?:current|selected|loading|loaded)\s+(?:course|track)(?:\s+name)?\s*[:=]\s*['\"]?(.+?)['\"]?\s*$", re.I),
                re.compile(r"(?:course|track)(?:\s+name)?\s*[:=]\s*['\"]?(.+?)['\"]?\s*$", re.I),
            ]
            for line in reversed(lines):
                for pattern in patterns:
                    match = pattern.search(line.strip())
                    if match and 1 < len(match.group(1).strip()) < 100:
                        return normalize_course_name(match.group(1))
        except OSError:
            pass
    home = Path.home()
    if platform.system() == "Windows":
        roots = [home / "AppData/LocalLow/velocidrone/velocidrone"]
    elif platform.system() == "Darwin":
        roots = [home / "Library/Application Support/com.velocidrone.velocidrone"]
    else:
        roots = [home / ".config/unity3d/velocidrone/velocidrone"]
    for root in roots:
        # user11.db contains the live per-user selection. settings.db is the
        # factory/base database and may contain a different default track.
        for filename in ("user11.db", "settings.db"):
            path = root / filename
            try:
                db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.2)
                tables = [row[0] for row in db.execute("select name from sqlite_master where type='table'")]
                # VelociDrone stores the active selection in sim_states and
                # resolves it against tracks.id (the game uses this relation).
                if "sim_states" in tables and "tracks" in tables:
                    current = db.execute(
                        "select name from tracks where id = "
                        "(select value from sim_states where name = 'track_id' limit 1) limit 1"
                    ).fetchone()
                    if current and isinstance(current[0], str) and current[0].strip():
                        db.close()
                        return normalize_course_name(current[0])
                candidates = []
                for table in tables:
                    if not re.fullmatch(r"[A-Za-z0-9_]+", table):
                        continue
                    columns = [row[1] for row in db.execute(f'pragma table_info("{table}")')]
                    if not any("track" in col.lower() or "course" in col.lower() for col in columns):
                        continue
                    for row in db.execute(f'select * from "{table}" limit 500'):
                        row_text = [str(value) for value in row if value is not None]
                        context = " ".join(row_text).lower()
                        score = 20 if ("current" in context or "selected" in context) else 0
                        for col, value in zip(columns, row):
                            if isinstance(value, str) and 1 < len(value.strip()) < 100:
                                col_low = col.lower()
                                if "trackname" in col_low or "coursename" in col_low:
                                    candidates.append((score + 100, value.strip()))
                                elif col_low in ("track", "course") or (col_low == "name" and score):
                                    candidates.append((score + 60, value.strip()))
                db.close()
                if candidates:
                    return normalize_course_name(max(candidates, key=lambda item: item[0])[1])
            except (OSError, sqlite3.Error):
                continue
    return None


def detect_aircraft_name(roots=None) -> str | None:
    """Read the selected model ID from the user DB, never the factory default."""
    if roots is None:
        if platform.system() == 'Windows':
            roots = [Path.home() / 'AppData/LocalLow/velocidrone/velocidrone']
        elif platform.system() == 'Darwin':
            roots = [Path.home() / 'Library/Application Support/com.velocidrone.velocidrone']
        else:
            roots = []
    for root in roots:
        root = Path(root)
        try:
            with closing(sqlite3.connect((root / 'user11.db').resolve().as_uri() + '?mode=ro', uri=True, timeout=0.2)) as user:
                selected = user.execute("select value from sim_states where name='model_id' limit 1").fetchone()
            if not selected:
                continue
            with closing(sqlite3.connect((root / 'settings.db').resolve().as_uri() + '?mode=ro', uri=True, timeout=0.2)) as base:
                model = base.execute('select name from models where model_id=? limit 1', (selected[0],)).fetchone()
            if model and isinstance(model[0], str) and model[0].strip():
                return model[0].strip()
        except (OSError, sqlite3.Error):
            continue
    return None


@dataclass
class LapEvent:
    kind: str
    seconds: float
    track: str
    race_elapsed: float | None = None
    estimated: bool = False
    manual: bool = False
    aircraft: str = ''


class Store:
    def __init__(self) -> None:
        self.data = {"settings": {}, "bests": {}}
        if DATA_FILE.exists():
            try:
                self.data.update(json.loads(DATA_FILE.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                pass
        if self.data.get("timing_version") != TIMING_VERSION:
            # Older versions subtracted the first received gate timestamp from
            # lap 1 and the three-lap total. Keep settings, but rebase records.
            self.data["bests"] = {}
            self.data["timing_version"] = TIMING_VERSION

    def save(self) -> None:
        DATA_FILE.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def update_best(self, event: LapEvent) -> tuple[bool, float | None]:
        key = f"{event.track}|{event.kind}"
        old = self.data.setdefault("bests", {}).get(key)
        if old is None or event.seconds < float(old):
            self.data["bests"][key] = event.seconds
            self.save()
            return True, None if old is None else float(old)
        return False, float(old)


class LogParser:
    TRACK_PATTERNS = [
        re.compile(r"(?:track|course)(?:\s+name)?\s*[:=]\s*['\"]?(.+?)['\"]?\s*$", re.I),
        re.compile(r"loading\s+track\s+['\"]?(.+?)['\"]?\s*$", re.I),
    ]
    EVENT_PATTERNS = [
        ("three_lap", re.compile(r"(?:3|three)\s*lap(?:\s+(?:race\s+)?time)?\s*[:=]\s*(\d+(?::\d+)?[.,]\d+)", re.I)),
        ("lap", re.compile(r"(?:lap(?:\s+time)?|laptime)\s*[:=]\s*(\d+(?::\d+)?[.,]\d+)", re.I)),
    ]

    def __init__(self) -> None:
        self.track = "Unknown Track"

    @staticmethod
    def seconds(value: str) -> float:
        value = value.replace(",", ".")
        if ":" in value:
            minutes, seconds = value.split(":", 1)
            return int(minutes) * 60 + float(seconds)
        return float(value)

    def parse(self, line: str) -> LapEvent | None:
        clean = line.strip()
        for pattern in self.TRACK_PATTERNS:
            match = pattern.search(clean)
            if match:
                self.track = match.group(1).strip()
        for kind, pattern in self.EVENT_PATTERNS:
            match = pattern.search(clean)
            if match:
                return LapEvent(kind, self.seconds(match.group(1)), self.track)
        return None


class VeloRaceParser:
    """Convert VelociDrone WebSocket race messages into completed-lap events."""

    def __init__(self) -> None:
        self.track = "Unknown Track"
        self.pilots: dict[str, dict] = {}
        self.race_laps = 3

    def _find_track(self, value) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                compact = key.lower().replace("_", "").replace(" ", "")
                if compact in ("track", "trackname", "coursename") and isinstance(child, str) and child.strip():
                    self.track = child.strip()
                self._find_track(child)
        elif isinstance(value, list):
            for child in value:
                self._find_track(child)

    def feed(self, message: str | dict) -> list[LapEvent]:
        try:
            if isinstance(message, (bytes, bytearray)):
                message = message.decode("utf-8", errors="replace")
            data = json.loads(message) if isinstance(message, str) else message
        except (json.JSONDecodeError, UnicodeDecodeError):
            return []
        self._find_track(data)
        lowered = {str(key).lower(): value for key, value in data.items()} if isinstance(data, dict) else {}
        status = lowered.get("racestatus")
        if isinstance(status, dict):
            if str(status.get("raceAction", "")).lower() in ("start", "abort", "reset", "restart", "cancel", "stop", "race aborted", "race reset"):
                self.pilots.clear()
            return []
        race_type = lowered.get("racetype")
        if isinstance(race_type, dict):
            try:
                self.race_laps = int(race_type.get("raceLaps", 3))
            except (TypeError, ValueError):
                pass
        race = lowered.get("racedata")
        if not isinstance(race, dict):
            return []
        events: list[LapEvent] = []
        for name, pilot in race.items():
            if not isinstance(pilot, dict):
                continue
            try:
                lap = int(pilot.get("lap", 0))
                timing = Decimal(str(pilot.get("time", "")))
                if not timing.is_finite() or timing < 0 or lap < 1:
                    continue
                finished = str(pilot.get("finished", "false")).lower() == "true"
            except (TypeError, ValueError, InvalidOperation):
                continue
            state = self.pilots.get(name)
            if state is None:
                # Only lap 1 has a known start when connecting mid-flight.
                state = {"lap": lap, "boundary": Decimal(0) if lap == 1 else None,
                         "last": Decimal(0), "finished": False}
                self.pilots[name] = state
            if state["finished"] or lap < state["lap"] or timing < state["last"]:
                continue
            completed = []
            at_boundary = str(pilot.get("gate", "")) == "1" and not finished
            if lap > state["lap"]:
                if lap == state["lap"] + 1 and at_boundary and state["boundary"] is not None:
                    completed.append(timing - state["boundary"])
                # Never mistake multiple missing laps for one fast-lap result.
                state["boundary"] = timing if at_boundary else None
                state["lap"] = lap
            elif state["boundary"] is None and at_boundary:
                state["boundary"] = timing
            if finished and state["boundary"] is not None:
                completed.append(timing - state["boundary"])
            state["last"] = timing
            state["finished"] = finished
            for duration in completed:
                if duration > Decimal("0.05"):
                    events.append(LapEvent("lap", float(round(duration, 3)), self.track))
            # The finish packet already carries the official cumulative time;
            # it remains authoritative even when intermediate packets were lost.
            if finished and lap == 3 and self.race_laps == 3 and timing > 0:
                events.append(LapEvent("three_lap", float(round(timing, 3)), self.track))
        return events


def default_log_path() -> str:
    home = Path.home()
    candidates = []
    if platform.system() == "Windows":
        candidates.append(home / "AppData/LocalLow/velocidrone/velocidrone/Player.log")
    elif platform.system() == "Darwin":
        candidates.append(home / "Library/Logs/velocidrone/velocidrone/Player.log")
        candidates.append(home / "Library/Logs/Unity/Player.log")
    return str(next((p for p in candidates if p.exists()), candidates[0] if candidates else ""))


def velocidrone_running() -> bool:
    try:
        if platform.system() == "Windows":
            out = subprocess.check_output(
                ["tasklist", "/FO", "CSV", "/NH"],
                text=True,
                errors="ignore",
                **hidden_process_options(),
            )
            return "velocidrone" in out.lower()
        out = subprocess.check_output(
            ["pgrep", "-ifl", "velocidrone"], text=True, errors="ignore"
        )
        return bool(out.strip())
    except (subprocess.SubprocessError, OSError):
        return False


class Obs:
    def __init__(self, host: str, port: int, password: str) -> None:
        self.host = host
        self.port = int(port)
        self.password = password
        self.socket = None
        self.request_lock = threading.Lock()
        self._connect()

    def _connect(self) -> None:
        try:
            import websocket
        except ImportError as exc:
            raise RuntimeError("録画に必要な機能を読み込めません。配布ZIPを新しいフォルダに展開し直してください。") from exc
        if self.socket is not None:
            try:
                self.socket.close()
            except Exception:
                pass
        self.socket = websocket.create_connection(
            f"ws://{self.host}:{self.port}", timeout=4,
            subprotocols=["obswebsocket.json"],
        )
        hello = json.loads(self.socket.recv())
        if hello.get("op") != 0:
            raise RuntimeError("OBS WebSocketからHelloを受信できませんでした")
        identify = {"rpcVersion": 1}
        authentication = hello.get("d", {}).get("authentication")
        if authentication:
            salt = authentication.get("salt", "")
            challenge = authentication.get("challenge", "")
            secret = base64.b64encode(
                hashlib.sha256((self.password + salt).encode("utf-8")).digest()
            ).decode("ascii")
            identify["authentication"] = base64.b64encode(
                hashlib.sha256((secret + challenge).encode("utf-8")).digest()
            ).decode("ascii")
        self.socket.send(json.dumps({"op": 1, "d": identify}))
        identified = json.loads(self.socket.recv())
        if identified.get("op") != 2:
            raise RuntimeError("OBS WebSocketの認証に失敗しました。パスワードを確認してください")

    def _request_once(self, request_type: str, request_data: dict | None = None) -> dict:
        request_id = str(uuid.uuid4())
        data = {"requestType": request_type, "requestId": request_id}
        if request_data is not None:
            data["requestData"] = request_data
        self.socket.send(json.dumps({"op": 6, "d": data}))
        deadline = time.time() + 8
        while time.time() < deadline:
            response = json.loads(self.socket.recv())
            if response.get("op") != 7:
                continue
            payload = response.get("d", {})
            if payload.get("requestId") != request_id:
                continue
            status = payload.get("requestStatus", {})
            if not status.get("result", False):
                code = status.get("code", "?")
                comment = status.get("comment", "")
                raise RuntimeError(f"OBS {request_type} エラー {code}: {comment}")
            return payload.get("responseData", {}) or {}
        raise RuntimeError(f"OBS {request_type} の応答がありません")

    def request(self, request_type: str, request_data: dict | None = None) -> dict:
        with self.request_lock:
            try:
                return self._request_once(request_type, request_data)
            except RuntimeError:
                raise
            except Exception:
                self._connect()
                return self._request_once(request_type, request_data)

    def start_buffer(self) -> None:
        try:
            if self.request("GetReplayBufferStatus").get("outputActive"):
                return
        except Exception:
            pass
        try:
            self.request("StartReplayBuffer")
        except Exception as exc:
            if "already" not in str(exc).lower() and "running" not in str(exc).lower():
                raise
        self.wait_until_running()

    def wait_until_running(self, timeout: float = 8.0) -> None:
        deadline = time.time() + timeout
        last_error = None
        while time.time() < deadline:
            try:
                if self.request("GetReplayBufferStatus").get("outputActive"):
                    return
            except Exception as exc:
                last_error = exc
            time.sleep(0.2)
        detail = f": {last_error}" if last_error else ""
        raise RuntimeError("OBSのリプレイバッファ開始を確認できませんでした" + detail)

    def stop_buffer(self) -> None:
        if not self.request("GetReplayBufferStatus").get("outputActive"):
            return
        self.request("StopReplayBuffer")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if not self.request("GetReplayBufferStatus").get("outputActive"):
                return
            time.sleep(0.1)
        raise RuntimeError("OBSのリプレイバッファの停止を確認できませんでした")

    def save(self) -> Path:
        self.wait_until_running()
        previous = None
        try:
            previous = self.request("GetLastReplayBufferReplay").get("savedReplayPath")
        except Exception:
            pass
        previous_stat = None
        if previous:
            try:
                old = Path(previous).stat()
                previous_stat = (old.st_mtime_ns, old.st_size)
            except OSError:
                pass
        # SaveReplayBuffer itself intentionally has no response data. The path
        # becomes available through GetLastReplayBufferReplay after finalizing.
        requested_ns = time.time_ns()
        self.request("SaveReplayBuffer")
        deadline = time.time() + 20
        last_path = None
        last_changed_stat = None
        stable_samples = 0
        while time.time() < deadline:
            try:
                last_path = self.request("GetLastReplayBufferReplay").get("savedReplayPath")
                if last_path and last_path != previous:
                    if previous:
                        return Path(last_path)
                    # If the previous path was unavailable, do not accept an
                    # older file merely because OBS reports it after a request.
                    try:
                        stat = Path(last_path).stat()
                        if stat.st_size > 0 and stat.st_mtime_ns >= requested_ns - 1_000_000_000:
                            return Path(last_path)
                    except OSError:
                        pass
                if last_path and last_path == previous:
                    try:
                        stat = Path(last_path).stat()
                        current_stat = (stat.st_mtime_ns, stat.st_size)
                        if stat.st_size > 0 and current_stat != previous_stat:
                            stable_samples = stable_samples + 1 if current_stat == last_changed_stat else 1
                            last_changed_stat = current_stat
                            if stable_samples >= 5:
                                return Path(last_path)
                    except OSError:
                        pass
            except Exception:
                pass
            time.sleep(0.25)
        raise RuntimeError("OBSの新しい保存動画を20秒以内に確認できませんでした。OBSの保存先とリプレイバッファを確認してください")


def safe_name(value: str) -> str:
    value = value.replace("+", "_")
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .") or "Unknown Track"


def output_directory(source: Path, track: str, folder_mode: str, folder_template=None, event=None, saved_at=None) -> Path:
    course = safe_name(track)
    if folder_mode == "custom":
        kind = "1lap" if event and event.kind == "lap" else "3lap"
        folder = render_name(folder_template or '{course}', track, kind, event.seconds if event else 0, saved_at,
                             pending=bool(event and event.estimated), aircraft=event.aircraft if event else '')
        output_dir = source.parent / folder
    elif folder_mode == "aircraft":
        folder = render_name('{aircraft}', track, '3lap', 0, saved_at,
                             aircraft=event.aircraft if event else '')
        output_dir = source.parent / folder
    elif folder_mode == "date":
        output_dir = source.parent / time.strftime("%Y-%m-%d")
    elif folder_mode == "none":
        output_dir = source.parent
    else:
        output_dir = source.parent / course
    output_dir.mkdir(parents=False, exist_ok=True)
    return output_dir


def available_target(path: Path) -> Path:
    if not path.exists():
        return path
    for number in range(2, 1000):
        candidate = path.with_name(f"{path.stem}_{number}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError("保存先のファイル名を確保できませんでした")


def rename_clip_metadata(
    path: Path,
    old_track: str,
    old_kind: str,
    old_seconds: float,
    new_track: str,
    new_kind: str,
    new_seconds: float,
) -> Path:
    """Rename only metadata tokens that are actually present in a clip name."""
    if not path.is_file():
        raise FileNotFoundError(f"動画ファイルが見つかりません: {path}")
    metadata = read_metadata(path)
    if metadata.get('template') and not metadata.get('pending'):
        saved_at = datetime.fromisoformat(metadata['saved_at'])
        kind = '1lap' if new_kind == 'lap' else '3lap'
        aircraft = metadata.get('aircraft', '')
        stem = render_name(metadata['template'], new_track, kind, new_seconds, saved_at, aircraft=aircraft)
        desired = path.with_name(stem + path.suffix)
        if desired == path:
            write_metadata(path, new_track, kind, new_seconds, saved_at, metadata['template'], aircraft=aircraft)
            return path
        target = available_target(desired)
        path.rename(target)
        write_metadata(target, new_track, kind, new_seconds, saved_at, metadata['template'], aircraft=aircraft)
        delete_metadata(path)
        return target
    stem = path.stem
    changed = False
    old_course = safe_name(old_track)
    new_course = safe_name(new_track)
    if old_course != new_course and stem.startswith(old_course + "_"):
        stem = new_course + stem[len(old_course):]
        changed = True
    old_kind_token = "1lap" if old_kind == "lap" else "3lap"
    new_kind_token = "1lap" if new_kind == "lap" else "3lap"
    if old_kind_token != new_kind_token and f"_{old_kind_token}_" in stem:
        stem = stem.replace(f"_{old_kind_token}_", f"_{new_kind_token}_", 1)
        changed = True
    old_time_token = f"{float(old_seconds):.3f}s"
    new_time_token = f"{float(new_seconds):.3f}s"
    if old_time_token != new_time_token and old_time_token in stem:
        stem = stem.replace(old_time_token, new_time_token, 1)
        changed = True
    if not changed:
        return path
    target = available_target(path.with_name(stem + path.suffix))
    path.rename(target)
    return target


def archive_replay(
    source: Path,
    event: LapEvent,
    folder_mode: str = "course",
    delete_source: bool = True,
    pending: bool = False,
    folder_template=None,
    filename_template=None,
) -> Path:
    """Preserve the OBS replay without decoding or re-encoding it."""
    deadline = time.time() + 20
    while time.time() < deadline:
        if source.exists() and source.stat().st_size > 0:
            break
        time.sleep(0.25)
    if not source.exists() or source.stat().st_size <= 0:
        raise RuntimeError(f"OBSの保存動画が見つかりません: {source}")
    saved_at = datetime.now()
    output_dir = output_directory(source, event.track, folder_mode, folder_template, event, saved_at)
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    extension = source.suffix or ".mkv"
    course = safe_name(event.track)
    if pending:
        kind = "1lap" if event.kind == "lap" else "3lap"
        filename = f"{course}_{kind}_TIME_INPUT_REQUIRED_{stamp}{extension}"
    else:
        kind = "1lap" if event.kind == "lap" else "3lap"
        filename = f"{course}_{kind}_{event.seconds:.3f}s_{stamp}{extension}"
    if filename_template and not pending:
        filename = render_name(filename_template, event.track, "1lap" if event.kind == 'lap' else '3lap', event.seconds, saved_at, aircraft=event.aircraft) + extension
    target = available_target(output_dir / filename)
    if source.resolve() == target.resolve():
        return source
    if delete_source:
        shutil.move(str(source), str(target))
    else:
        shutil.copy2(source, target)
    write_metadata(target, event.track, "1lap" if event.kind == 'lap' else '3lap', event.seconds, saved_at,
                   filename_template or '', pending=pending, aircraft=event.aircraft)
    return target


def delete_older_clips(target: Path, event: LapEvent, search_root: Path | None = None, protected_paths=()) -> int:
    """Delete only older FPVSIM-Clipper outputs for the same course and kind."""
    kind = "1lap" if event.kind == "lap" else "3lap"
    prefix = f"{safe_name(event.track)}_{kind}_"
    removed = 0
    try:
        parent = (search_root or target.parent).resolve()
        current = target.resolve()
        protected = {Path(path).resolve() for path in protected_paths}
        for candidate in parent.rglob("*.mp4"):
            if not candidate.is_file() or candidate.suffix.lower() != ".mp4":
                continue
            resolved = candidate.resolve()
            if not resolved.is_relative_to(parent):
                continue
            if resolved in protected:
                continue
            metadata = read_metadata(candidate)
            if "TIME_INPUT_REQUIRED" in candidate.name or metadata.get('pending'):
                continue
            matches = (metadata.get('course') == event.track and metadata.get('kind') == kind) if metadata else candidate.name.startswith(prefix)
            if matches and resolved != current:
                candidate.unlink()
                delete_metadata(candidate)
                removed += 1
    except OSError:
        pass
    return removed


def trim_clip(source: Path, duration: float, event: LapEvent, delete_source: bool = True,
              folder_mode: str = "course", end_offset: float = 0.0, precise: bool = True,
              folder_template=None, filename_template=None) -> Path:
    """Cut an exact interval using the native OS media engine."""
    from native_media import render_interval
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if source.is_file() and source.stat().st_size > 0:
            break
        time.sleep(0.25)
    if not source.is_file() or source.stat().st_size <= 0:
        raise RuntimeError(f"OBSの保存動画が見つかりません: {source}")
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    kind = "1lap" if event.kind == "lap" else "3lap"
    saved_at = datetime.now()
    output_dir = output_directory(source, event.track, folder_mode, folder_template, event, saved_at)
    template = filename_template or DEFAULT_FILE_TEMPLATE
    if template.lower().endswith('.mp4'):
        template = template[:-4]
    stem = render_name(template, event.track, kind, event.seconds, saved_at, aircraft=event.aircraft)
    if stem.lower().endswith('.mp4'):
        stem = stem[:-4]
    target = available_target(output_dir / (stem + '.mp4'))
    temp = target.with_name(target.stem + ".working.mp4")
    try:
        render_interval(source, temp, duration, end_offset, precise=precise)
        if not temp.is_file() or temp.stat().st_size == 0:
            raise RuntimeError("切り出した動画が空です")
        temp.replace(target)
        write_metadata(target, event.track, kind, event.seconds, saved_at, template, aircraft=event.aircraft)
    except Exception as exc:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError(
            f"動画を切り出せませんでした: {exc}。元動画は保持しています。"
            "OBSの形式はH.264/AACのMP4またはMOVを推奨します"
        ) from exc
    if delete_source:
        try:
            source.unlink()
        except OSError:
            pass
    return target
