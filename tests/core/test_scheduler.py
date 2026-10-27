"""Slots per resource (core/scheduler.py): the jobs take turns."""
import threading
import time
import unittest
from unittest import mock

import config
from core import scheduler


class Slots(unittest.TestCase):
    def setUp(self):
        self.patch = mock.patch.object(config, "SLOTS", {"x": 2, "twitch": 4, "whisper": 1, "analysis": 2, "nvenc": 2})
        self.patch.start()
        scheduler.reset()

    def tearDown(self):
        self.patch.stop()
        scheduler.reset()

    def test_at_most_the_capacity_at_once(self):
        lock, now, peak = threading.Lock(), [0], [0]

        def work():
            with scheduler.slot("x"):
                with lock:
                    now[0] += 1
                    peak[0] = max(peak[0], now[0])
                time.sleep(0.1)
                with lock:
                    now[0] -= 1
        threads = [threading.Thread(target=work) for _ in range(7)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(peak[0], 2)
        snap = scheduler.snapshot()["x"]
        self.assertEqual((snap["capacity"], snap["running"], snap["waiting"], snap["peak"], snap["runs"]),
                         (2, 0, 0, 2, 7))

    def test_first_come_first_served_and_priority(self):
        order = []
        gate = threading.Event()

        def holder():
            with scheduler.slot("whisper"):
                gate.wait(5)

        def job(name, priority=0):
            with scheduler.slot("whisper", priority=priority):
                order.append(name)
        h = threading.Thread(target=holder)
        h.start()
        time.sleep(0.05)
        threads = []
        for name, priority in (("a", 0), ("b", 0), ("urgent", -1), ("c", 0)):
            t = threading.Thread(target=job, args=(name, priority))
            t.start()
            threads.append(t)
            time.sleep(0.05)               # they arrive in this order
        gate.set()
        for t in [h] + threads:
            t.join()
        self.assertEqual(order, ["urgent", "a", "b", "c"])

    def test_the_wait_is_kept_per_job(self):
        gate = threading.Event()
        threading.Thread(target=lambda: scheduler.get("whisper").acquire() and None).start()   # takes the only slot
        time.sleep(0.05)

        def release_later():
            time.sleep(0.3)
            scheduler.get("whisper").release()
        threading.Thread(target=release_later).start()
        with scheduler.slot("whisper", "job-1") as waited:
            pass
        self.assertGreaterEqual(waited, 0.2)
        waits = scheduler.take_waits("job-1")
        self.assertGreaterEqual(waits["whisper"], 0.2)
        self.assertEqual(scheduler.take_waits("job-1"), {})                # taken once
        with scheduler.slot("whisper", "job-2"):                            # free: no wait to keep
            pass
        self.assertEqual(scheduler.take_waits("job-2"), {})
        gate.set()

    def test_capacity_comes_from_config_and_can_change(self):
        self.assertEqual((scheduler.capacity_of("twitch"), scheduler.capacity_of("whisper")), (4, 1))
        self.assertEqual(scheduler.capacity_of("unknown"), 1)
        res = scheduler.get("x")
        res.acquire()
        res.acquire()
        got = []
        t = threading.Thread(target=lambda: (res.acquire(), got.append(1), res.release()))
        t.start()
        time.sleep(0.1)
        self.assertEqual(got, [])                                           # full
        res.set_capacity(3)                                                  # one more fits
        t.join(2)
        self.assertEqual(got, [1])
        res.release()
        res.release()

    def test_a_slot_is_given_back_when_the_job_fails(self):
        with self.assertRaises(ValueError):
            with scheduler.slot("whisper"):
                raise ValueError("boom")
        self.assertEqual(scheduler.snapshot()["whisper"]["running"], 0)


if __name__ == "__main__":
    unittest.main()
