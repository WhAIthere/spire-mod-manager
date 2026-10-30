"""Bounded in-memory undo/redo for editor state; no game file writes."""
from copy import deepcopy


class History:
    def __init__(self, limit=100):
        self.limit = limit
        self.states = []
        self.index = -1

    def reset(self, state):
        self.states = [deepcopy(state)]
        self.index = 0

    def record(self, state):
        if self.index >= 0 and state == self.states[self.index]:
            return False
        self.states = self.states[:self.index + 1] + [deepcopy(state)]
        if len(self.states) > self.limit + 1:
            self.states.pop(0)
        self.index = len(self.states) - 1
        return True

    def undo(self):
        if self.index <= 0:
            return None
        self.index -= 1
        return deepcopy(self.states[self.index])

    def redo(self):
        if self.index + 1 >= len(self.states):
            return None
        self.index += 1
        return deepcopy(self.states[self.index])
