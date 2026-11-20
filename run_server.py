import threading
import time

from seat_db import SeatDatabase, run_server


def timer_loop(db, every_seconds=30):
    while True:
        released = db.check_timers()
        if released:
            print("Auto-released seats:", released)
        time.sleep(every_seconds)


if __name__ == "__main__":
    db = SeatDatabase(n_tables=1, seats_per_table=1, use_rfid=False)
    threading.Thread(target=timer_loop, args=(db,), daemon=True).start()
    print("Server starting on port 5000. Open /status in a browser to see the seat table.")
    run_server(db, port=5000)
