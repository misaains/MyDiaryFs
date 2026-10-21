import json
import os
import sys
import time
from pathlib import Path

from config import ENV_FILE, apply_proxy_environment, configuration_errors, read_settings

STATE = Path(os.getenv("MYDIARY_STATUS_FILE", str(Path.cwd() / "runtime-status.json")))


def set_status(state, missing=None):
    temporary = STATE.with_suffix(".tmp")
    temporary.write_text(json.dumps({"state": state, "missing": missing or []}) + "\n")
    temporary.replace(STATE)


def main():
    os.umask(0o077)
    last_errors = None
    while True:
        settings = read_settings()
        errors = configuration_errors(settings)
        if not errors:
            break
        if errors != last_errors:
            print("Waiting for configuration in " + str(ENV_FILE) + ": " + ", ".join(errors), flush=True)
            last_errors = errors
            set_status("waiting-for-configuration", errors)
        time.sleep(10)

    # Route requests/FatSecret and HTTPX through the same existing proxy.
    apply_proxy_environment(settings)
    set_status("starting-bot")
    print("Configuration complete; starting the private diary bot.", flush=True)
    os.execv(sys.executable, [sys.executable, "-u", str(Path(__file__).with_name("bot.py"))])


if __name__ == "__main__":
    main()
