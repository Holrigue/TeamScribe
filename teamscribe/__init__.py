"""TeamScribe — record, transcribe and summarize Teams meetings, then push
action items to Microsoft Planner.

The pipeline is three stages, each runnable on its own from the CLI:

    1. record      -> capture.py     (WASAPI loopback + mic -> 16 kHz mono wav)
    2. summarize   -> transcribe.py + summarize.py
    3. push-tasks  -> planner.py      (Microsoft Graph)
"""

__version__ = "2.1.4"
