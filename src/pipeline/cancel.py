import threading


def make_cancel_event() -> threading.Event:
    return threading.Event()
