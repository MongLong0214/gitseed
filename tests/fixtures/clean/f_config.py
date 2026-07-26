DEFAULTS = {"host": "127.0.0.1", "port": 8080, "retries": 3}
def merge(overrides): return {**DEFAULTS, **overrides}
