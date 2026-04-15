"""ThrowVision – TF-Luna LiDAR Distance Sensor Reader.

Continuously reads distance from a TF-Luna sensor over serial in a
background thread.  Used for oche (foul-line) distance checking.

The TF-Luna outputs 9-byte packets at up to 250 Hz:
  0x59 0x59 Dist_L Dist_H Str_L Str_H Temp_L Temp_H Checksum
"""

import threading
import time

try:
    import serial
    _SERIAL_AVAILABLE = True
except ImportError:
    _SERIAL_AVAILABLE = False


class TFLunaReader:
    """Thread-safe TF-Luna distance reader with background polling."""

    def __init__(self, port: str, baud: int = 115200) -> None:
        self._port = port
        self._baud = baud
        self._ser = None
        self._thread = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # Latest readings (protected by _lock)
        self._distance_cm: int = 0
        self._strength: int = 0
        self._temperature: float = 0.0
        self._last_read_ts: float = 0.0
        self._connected: bool = False

    # ── Public properties (thread-safe reads) ──────────────────────────

    @property
    def distance_cm(self) -> int:
        with self._lock:
            return self._distance_cm

    @property
    def strength(self) -> int:
        with self._lock:
            return self._strength

    @property
    def temperature(self) -> float:
        with self._lock:
            return self._temperature

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    @property
    def is_stale(self) -> bool:
        """True if the last reading is older than 1 second."""
        with self._lock:
            if self._last_read_ts == 0.0:
                return True
            return (time.monotonic() - self._last_read_ts) > 1.0

    @property
    def port(self) -> str:
        return self._port

    # ── Lifecycle ──────��───────────────────────────────────────────────

    def start(self) -> bool:
        """Open the serial port and start the reader thread.

        Returns True if the sensor was opened successfully.
        """
        if not _SERIAL_AVAILABLE:
            print("[TF-LUNA] pyserial not installed — sensor disabled")
            return False

        if not self._port:
            print("[TF-LUNA] No COM port configured — sensor disabled")
            return False

        try:
            self._ser = serial.Serial(self._port, self._baud, timeout=1)
            with self._lock:
                self._connected = True
            print(f"[TF-LUNA] Connected on {self._port} at {self._baud} baud")
        except serial.SerialException as e:
            print(f"[TF-LUNA] Failed to open {self._port}: {e}")
            with self._lock:
                self._connected = False
            return False

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._read_loop, name="tfluna-reader", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        """Stop the reader thread and close the serial port."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
        with self._lock:
            self._connected = False
        print("[TF-LUNA] Stopped")

    # ── Background reader ──────────────────────────────���───────────────

    def _read_loop(self) -> None:
        """Daemon thread: continuously read 9-byte packets from TF-Luna."""
        ser = self._ser
        while not self._stop_event.is_set():
            try:
                # Flush stale data so we always get the latest reading
                if ser.in_waiting > 27:  # >3 packets queued
                    ser.reset_input_buffer()

                # Sync to header bytes 0x59 0x59
                b = ser.read(1)
                if not b or b[0] != 0x59:
                    continue
                b = ser.read(1)
                if not b or b[0] != 0x59:
                    continue

                # Read remaining 7 bytes
                data = ser.read(7)
                if len(data) != 7:
                    continue

                # Verify checksum (lower 8 bits of sum of first 8 bytes)
                expected = (0x59 + 0x59 + sum(data[:6])) & 0xFF
                if data[6] != expected:
                    continue

                dist = data[0] | (data[1] << 8)
                strength = data[2] | (data[3] << 8)
                temp = (data[4] | (data[5] << 8)) / 8.0 - 256.0

                with self._lock:
                    self._distance_cm = dist
                    self._strength = strength
                    self._temperature = temp
                    self._last_read_ts = time.monotonic()

            except serial.SerialException as e:
                print(f"[TF-LUNA] Serial error: {e}")
                with self._lock:
                    self._connected = False
                # Try to reconnect after a brief pause
                time.sleep(1.0)
                try:
                    ser.close()
                except Exception:
                    pass
                try:
                    ser.open()
                    with self._lock:
                        self._connected = True
                    print(f"[TF-LUNA] Reconnected on {self._port}")
                except Exception:
                    pass
            except Exception:
                # Don't crash the thread on unexpected errors
                time.sleep(0.1)


# ---------------------------------------------------------------------------
# Standalone test (run: python tfluna.py COM7)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    port = sys.argv[1] if len(sys.argv) > 1 else "COM7"
    reader = TFLunaReader(port)
    if not reader.start():
        print("Failed to start — check port and pyserial installation")
        sys.exit(1)

    print(f"Reading from {port}… (Ctrl+C to stop)\n")
    try:
        while True:
            time.sleep(0.1)
            d = reader.distance_cm
            s = reader.strength
            t = reader.temperature
            stale = " (STALE)" if reader.is_stale else ""
            print(f"  Distance: {d:>4} cm | Strength: {s:>5} | "
                  f"Temp: {t:>5.1f} C{stale}", end="\r")
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        reader.stop()
