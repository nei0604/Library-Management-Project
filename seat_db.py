"""
Smart Library Seat Management: pandas database layer.

Sensors (ESP32) call these methods, directly or via the small Flask server
at the bottom of this file. Data is saved to CSV after every change.

Seat status values:
    free      nobody assigned to the seat
    occupied  student assigned and FSR detects pressure
    away      student assigned, no pressure, release timer running
"""
import os
from datetime import datetime, timedelta

import pandas as pd

SEATS_FILE = "seats.csv"
EVENTS_FILE = "events.csv"
AWAY_TIMEOUT = timedelta(hours=1)       # change to 2 hours if needed
MOTION_GRACE = timedelta(minutes=10)    # PIR motion this recent blocks release

SEAT_COLS = ["table_id", "seat_id", "student_id", "status", "pressure",
             "check_in_time", "last_detected_time", "timer_start"]
EVENT_COLS = ["time", "table_id", "seat_id", "student_id", "event"]
TIME_COLS = ["check_in_time", "last_detected_time", "timer_start"]


class SeatDatabase:
    def __init__(self, n_tables=1, seats_per_table=4,
                 seats_file=SEATS_FILE, events_file=EVENTS_FILE,
                 use_rfid=False):
        self.use_rfid = use_rfid
        self.seats_file = seats_file
        self.events_file = events_file
        self.last_motion = {}  # table_id -> last PIR motion time

        if os.path.exists(seats_file):
            self.seats = pd.read_csv(seats_file, parse_dates=TIME_COLS,
                                     dtype={"student_id": "object"})
            self.seats["pressure"] = self.seats["pressure"].astype(bool)
        else:
            rows = [{"table_id": t, "seat_id": s, "student_id": None,
                     "status": "free", "pressure": False,
                     "check_in_time": pd.NaT, "last_detected_time": pd.NaT,
                     "timer_start": pd.NaT}
                    for t in range(1, n_tables + 1)
                    for s in range(1, seats_per_table + 1)]
            self.seats = pd.DataFrame(rows, columns=SEAT_COLS)
            for c in TIME_COLS:
                self.seats[c] = pd.to_datetime(self.seats[c])
            self.seats["student_id"] = self.seats["student_id"].astype("object")

        for c in TIME_COLS:
            self.seats[c] = pd.to_datetime(self.seats[c]).astype("datetime64[ns]")

        if os.path.exists(events_file):
            self.events = pd.read_csv(events_file, parse_dates=["time"],
                                      dtype={"student_id": "object"})
        else:
            self.events = pd.DataFrame(columns=EVENT_COLS)
        self.save()

    # ---------- helpers ----------
    def _idx(self, table_id, seat_id):
        m = (self.seats.table_id == table_id) & (self.seats.seat_id == seat_id)
        return self.seats.index[m][0]

    def _log(self, now, table_id, seat_id, student_id, event):
        self.events.loc[len(self.events)] = [now, table_id, seat_id,
                                             student_id, event]

    def save(self):
        self.seats.to_csv(self.seats_file, index=False)
        self.events.to_csv(self.events_file, index=False)

    # ---------- sensor inputs ----------
    def update_pressure(self, table_id, seat_id, pressed, now=None):
        """Called by the ESP32 whenever an FSR crosses its threshold."""
        now = now or datetime.now()
        i = self._idx(table_id, seat_id)
        s = self.seats
        s.at[i, "pressure"] = bool(pressed)
        if pressed:
            s.at[i, "last_detected_time"] = now
        status, student = s.at[i, "status"], s.at[i, "student_id"]

        if pressed and status == "free" and not self.use_rfid:
            s.at[i, "status"] = "occupied"
            s.at[i, "check_in_time"] = now
            self._log(now, table_id, seat_id, None, "seated")
            status = "occupied"

        if pressed and status == "away":           # student came back
            s.at[i, "status"] = "occupied"
            s.at[i, "timer_start"] = pd.NaT
            self._log(now, table_id, seat_id, student, "returned")
        elif not pressed and status == "occupied":  # student stepped away
            s.at[i, "status"] = "away"
            s.at[i, "timer_start"] = now
            self._log(now, table_id, seat_id, student, "away")
        self.save()

    def update_motion(self, table_id, now=None):
        """Called by the ESP32 when the PIR sensor detects motion."""
        self.last_motion[table_id] = now or datetime.now()

    def tap_card(self, table_id, student_id, now=None):
        """
        Called when an RFID card is read at a table.
        Returns a short message string (can be shown on an LCD/LED).
        """
        now = now or datetime.now()
        student_id = str(student_id)
        s = self.seats
        at_table = s[s.table_id == table_id]

        # Already holds a seat at this table -> check out (tap-out)
        mine = at_table[at_table.student_id == student_id]
        if not mine.empty:
            i = mine.index[0]
            seat = s.at[i, "seat_id"]
            s.loc[i, ["student_id", "check_in_time", "timer_start",
                      "last_detected_time"]] = [None, pd.NaT, pd.NaT, pd.NaT]
            s.at[i, "status"] = "free"
            self._log(now, table_id, seat, student_id, "check_out")
            self.save()
            return f"Seat {seat} released"

        # Holds a seat at another table -> reject
        if (s.student_id == student_id).any():
            return "You already hold a seat elsewhere"

        # New check-in: prefer a free seat where the FSR sees someone sitting
        free = at_table[at_table.status == "free"]
        if free.empty:
            return "No free seat at this table"
        pressed = free[free.pressure]
        i = (pressed if not pressed.empty else free).index[0]
        seat = s.at[i, "seat_id"]
        s.at[i, "student_id"] = student_id
        s.at[i, "check_in_time"] = now
        s.at[i, "last_detected_time"] = now
        if s.at[i, "pressure"]:
            s.at[i, "status"] = "occupied"
        else:  # tapped in but not seated yet: start the away timer
            s.at[i, "status"] = "away"
            s.at[i, "timer_start"] = now
        self._log(now, table_id, seat, student_id, "check_in")
        self.save()
        return f"Seat {seat} assigned"

    # ---------- timer ----------
    def check_timers(self, now=None):
        """Run every minute or so. Frees seats whose away timer has expired."""
        now = now or datetime.now()
        s = self.seats
        released = []
        for i in s.index[s.status == "away"]:
            expired = now - s.at[i, "timer_start"] >= AWAY_TIMEOUT
            t = s.at[i, "table_id"]
            recent_motion = (t in self.last_motion
                             and now - self.last_motion[t] < MOTION_GRACE)
            if expired and not recent_motion and not s.at[i, "pressure"]:
                seat, student = s.at[i, "seat_id"], s.at[i, "student_id"]
                s.loc[i, ["student_id", "check_in_time", "timer_start",
                          "last_detected_time"]] = [None, pd.NaT, pd.NaT, pd.NaT]
                s.at[i, "status"] = "free"
                self._log(now, t, seat, student, "auto_release")
                released.append((t, seat))
        if released:
            self.save()
        return released

    # ---------- queries ----------
    def availability(self):
        """Free / occupied / away seat counts per table."""
        return (self.seats.groupby(["table_id", "status"]).size()
                .unstack(fill_value=0))

    def free_seats(self):
        return self.seats[self.seats.status == "free"][["table_id", "seat_id"]]

    def usage_report(self):
        """Number of events of each type: handy for analysis in the report."""
        return self.events.groupby("event").size()


# ---------- optional: HTTP server for the ESP32 ----------
def run_server(db, port=5000):
    """pip install flask. ESP32 POSTs JSON to these endpoints over WiFi."""
    from flask import Flask, jsonify, request
    app = Flask(__name__)

    @app.post("/pressure")  # {"table": 1, "seat": 2, "pressed": true}
    def pressure():
        d = request.json
        db.update_pressure(d["table"], d["seat"], d["pressed"])
        return jsonify(ok=True)

    @app.post("/motion")    # {"table": 1}
    def motion():
        db.update_motion(request.json["table"])
        return jsonify(ok=True)

    @app.post("/card")      # {"table": 1, "student": "21CS045"}
    def card():
        d = request.json
        return jsonify(message=db.tap_card(d["table"], d["student"]))

    @app.get("/status")
    def status():
        return app.response_class(
            db.seats.to_json(orient="records", date_format="iso"),
            mimetype="application/json")

    app.run(host="0.0.0.0", port=port)


# ---------- demo with simulated radar input ----------
if __name__ == "__main__":
    for f in (SEATS_FILE, EVENTS_FILE):
        if os.path.exists(f):
            os.remove(f)

    db = SeatDatabase(n_tables=1, seats_per_table=1)
    t0 = datetime(2026, 10, 22, 10, 0)

    db.update_pressure(1, 1, True, t0)
    print("After sitting:\n", db.seats[["seat_id", "status"]])
    db.update_pressure(1, 1, False, t0 + timedelta(minutes=20))
    print("After leaving:\n", db.seats[["seat_id", "status"]])

    print("Released at +50 min:", db.check_timers(t0 + timedelta(minutes=50)))
    print("Released at +90 min:", db.check_timers(t0 + timedelta(minutes=90)))

    print("\nAvailability:\n", db.availability())
    print("\nEvent log:\n", db.events)
