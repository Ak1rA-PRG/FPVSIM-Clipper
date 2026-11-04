"""Distinguish a completed flight from reset/abort teardown messages."""


class FinishGate:
    CANCEL_ACTIONS = {"abort", "reset", "restart", "cancel", "stop", "race aborted", "race reset"}

    def __init__(self):
        self.active = False
        self.finished_pilot = False
        self.clock_started = False

    def feed(self, payload):
        if not isinstance(payload, dict):
            return False
        status = payload.get("racestatus", {})
        action = str(status.get("raceAction", "")).strip().lower() if isinstance(status, dict) else ""
        if action == "start":
            self.active = True
            self.finished_pilot = False
            self.clock_started = False
            return False
        if action in self.CANCEL_ACTIONS:
            self.active = False
            self.finished_pilot = False
            return False
        if not self.active:
            return False
        countdown = payload.get("countdown", {})
        if isinstance(countdown, dict) and str(countdown.get("countValue", "")) == "0":
            self.clock_started = True
        race = payload.get("racedata", {})
        if isinstance(race, dict):
            for pilot in race.values():
                if isinstance(pilot, dict) and str(pilot.get("finished", "")).lower() == "true":
                    # Does not depend on a valid time: missing-time finishes must still alert.
                    self.finished_pilot = True
        if action == "race finished":
            # Some sessions send an old completion during the next countdown.
            if not self.clock_started and not self.finished_pilot:
                return False
            # Preserve a started flight even if every race-data packet was
            # missing. Explicit abort/reset actions still cancel the run above.
            confirmed = self.finished_pilot or self.clock_started
            self.active = False
            self.finished_pilot = False
            return confirmed
        return False
