"""
Built-in plugin: current date and time.
Usage: PLUGIN: datetime
"""
import datetime

PLUGIN_NAME = "datetime"
PLUGIN_DESCRIPTION = "Get the current date, time, and day of the week. No args needed."


def execute(args: str) -> str:
    now = datetime.datetime.now()
    return (
        f"Current date/time: {now.strftime('%A, %B %d, %Y at %H:%M:%S')} "
        f"(local timezone)"
    )
