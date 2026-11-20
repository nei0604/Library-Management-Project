# Smart Library Seat Management: Backend

Python backend for the one-chair prototype. An ESP32 reads an mmWave radar (HLK-LD2410B-P) and reports whether the seat is occupied. This backend keeps the seat state in a pandas DataFrame, runs the "student stepped away" timer, and saves everything to CSV files.

## Files

| File | Purpose |
| --- | --- |
| `seat_db.py` | The database logic (`SeatDatabase` class) and the Flask server function `run_server()`. Running it directly starts a demo with simulated input |
| `run_server.py` | Starts the server for one table with one seat, plus a background thread that checks timers every 30 seconds |
| `seats.csv` | Current state of every seat (created automatically) |
| `events.csv` | Log of every change, for analysis and for your report (created automatically) |

## Requirements

- Python 3.9 or newer
- `pip install pandas flask`

## Run

```
python3 run_server.py
```

Use `python` instead of `python3` on Windows. The server listens on port 5000 on all network interfaces. Allow it through your firewall if asked.

Check that it works by opening `http://<laptop-IP>:5000/status` in a browser. You should see one seat with `"status":"free"`.

To find your laptop's IP address, run `ipconfig getifaddr en0` on a Mac or `ipconfig` on Windows (use the IPv4 address). Put this address in the `SERVER` line of the ESP32 code.

## How seat status works

| Status | Meaning |
| --- | --- |
| `free` | Nobody is using the seat |
| `occupied` | The radar detects a person |
| `away` | The radar no longer detects anyone, and the release timer is running |

Transitions:

1. `free` to `occupied`: the radar reports presence.
2. `occupied` to `away`: the radar reports no presence. The timer starts.
3. `away` to `occupied`: presence returns before the timer ends.
4. `away` to `free`: the timer runs out with no presence. The seat is released.

The ESP32 already waits for a stable radar reading (1.5 seconds to confirm presence, 10 seconds to confirm absence) before it sends anything, so brief flickers do not reach the backend.

## API

All requests and responses use JSON.

### `POST /pressure`

The ESP32 sends this when the confirmed seat state changes. The field is named `pressed` for historical reasons and means "a person is detected".

```
curl -X POST http://localhost:5000/pressure \
  -H "Content-Type: application/json" \
  -d '{"table": 1, "seat": 1, "pressed": true}'
```

Response: `{"ok": true}`

### `GET /status`

Returns every seat as a list of records.

```
curl http://localhost:5000/status
```

Example response:

```
[{"table_id":1,"seat_id":1,"student_id":null,"status":"occupied","pressure":true,
  "check_in_time":"2026-11-16T13:09:40.890","last_detected_time":"2026-11-16T13:09:40.927",
  "timer_start":null}]
```

### `POST /motion` and `POST /card`

These two endpoints exist from the earlier design (PIR sensor and RFID reader). The radar-only prototype does not use them, and they can be ignored.

## Data

### `seats.csv` columns

| Column | Description |
| --- | --- |
| `table_id`, `seat_id` | Which seat |
| `student_id` | Empty in the radar-only version |
| `status` | `free`, `occupied` or `away` |
| `pressure` | `true` when a person is currently detected |
| `check_in_time` | When the seat became occupied |
| `last_detected_time` | Last time presence was detected |
| `timer_start` | When the away timer started (empty if not running) |

### `events.csv` columns

`time`, `table_id`, `seat_id`, `student_id`, `event`

Event values in the radar-only version: `seated`, `away`, `returned`, `auto_release`.

The files are saved after every change and reloaded when the server restarts, so state survives a restart.

## Settings

Edit the constants at the top of `seat_db.py`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `AWAY_TIMEOUT` | 1 hour | How long a seat stays `away` before it is released. Use 2 hours if you prefer |
| `MOTION_GRACE` | 10 minutes | Only applies if `/motion` is used, which the radar-only version does not |

To change the number of tables or seats, edit the `SeatDatabase(...)` line in `run_server.py`. It is currently `n_tables=1, seats_per_table=1`.

If you change a setting, restart the server.

## Testing

**Demo with simulated input.** Running `python3 seat_db.py` plays a sit, leave and timer-expiry sequence with a fake clock and prints the results. Warning: it deletes any `seats.csv` and `events.csv` in the current folder before starting, so run it in a different folder from your real data.

**Test the live server with curl.** With the server running:

```
curl -X POST localhost:5000/pressure -H "Content-Type: application/json" -d '{"table":1,"seat":1,"pressed":true}'
curl localhost:5000/status
curl -X POST localhost:5000/pressure -H "Content-Type: application/json" -d '{"table":1,"seat":1,"pressed":false}'
curl localhost:5000/status
```

The status should change from `occupied` to `away`.

**Test the timer quickly.** Set `AWAY_TIMEOUT = timedelta(minutes=1)`, make the seat `away`, then wait about a minute. The background thread checks every 30 seconds, so allow up to 90 seconds. The seat should become `free`. Set it back afterwards.

## Limitations

- **No student identity.** Without RFID, a returning person cannot be told apart from someone else who took an `away` seat.
- **No tap-out.** A student who leaves for good keeps the seat marked `away` until the timer runs out.
- **Development server.** Flask's built-in server is fine for a demo on a local network. It has no login or encryption, so do not expose it to the internet.
- **Local network only.** The ESP32 and the laptop must be on the same WiFi, and the laptop's IP can change between sessions.
- **Single process.** Run only one copy of `run_server.py` at a time, since both read and write the same CSV files.
