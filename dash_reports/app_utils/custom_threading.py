# custom_threading.py

import threading

# Global counter for thread names
thread_counter = 0

# Lock to ensure thread-safe increment of the counter
thread_counter_lock = threading.Lock()


def initialize_custom_threading():
    # Patch the Thread class
    original_thread_init = threading.Thread.__init__

    def new_thread_init(self, *args, **kwargs):
        global thread_counter
        with thread_counter_lock:
            thread_counter += 1
            kwargs['name'] = f'T_{thread_counter}'
        original_thread_init(self, *args, **kwargs)

    threading.Thread.__init__ = new_thread_init
