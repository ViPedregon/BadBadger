"""Tests for player-facing command-line behavior."""

from io import StringIO
import unittest

from badbadger.cli import (
    WORKING_LABEL,
    command_needs_working_status,
    working_status,
)


class InteractiveStream(StringIO):
    def isatty(self) -> bool:
        return True


class CLIStatusTests(unittest.TestCase):
    def test_status_slug_wraps_commands_that_can_take_time(self):
        stream = InteractiveStream()
        with working_status(enabled=True, stream=stream):
            self.assertEqual(stream.getvalue(), WORKING_LABEL)
        self.assertEqual(
            stream.getvalue(),
            WORKING_LABEL + "\r" + (" " * len(WORKING_LABEL)) + "\r",
        )

    def test_status_slug_skips_instant_commands_and_redirected_output(self):
        self.assertFalse(command_needs_working_status("help"))
        self.assertFalse(command_needs_working_status("NPC Technician Orr"))
        self.assertTrue(command_needs_working_status("ask Orr what he knows"))
        stream = StringIO()
        with working_status(enabled=True, stream=stream):
            pass
        self.assertEqual(stream.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
