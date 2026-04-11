#!/usr/bin/env python3
"""
test_tui.py — Isolated test harness for asdaaas TUI components.

Tests widgets and interactions without a live agent session.
Uses Textual's headless pilot for automated testing including
mouse scroll simulation.

Usage:
    python3 test_tui.py              # Run all tests
    python3 test_tui.py --visual     # Launch visual test app for manual testing
"""

import asyncio
import json
import sys
import os
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

from asdaaas_tui import (
    AsdaaasTUI, Config, ContentScroll, AgentHeader, AgentTabBar,
    AgentMessage, UserMessage, ToolCallPanel, ThinkingBlock,
    PlanPanel, HookAnnotation, SystemAlert, SlashMenu, GazeSelector,
    MessageInput, DynamicFooter, Gruvbox
)

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static


# =============================================================================
# Minimal test app with synthetic content
# =============================================================================

class TestScrollApp(App):
    """Minimal app for testing scroll behavior."""

    CSS = """
    ContentScroll {
        height: 1fr;
        scrollbar-size: 1 1;
    }
    Static {
        height: auto;
    }
    """

    def compose(self) -> ComposeResult:
        yield ContentScroll(id="scroll")

    def on_mount(self) -> None:
        scroll = self.query_one("#scroll", ContentScroll)
        # Add 50 lines of content
        for i in range(50):
            scroll.mount(Static(f"Line {i}: " + "x" * 60))


# =============================================================================
# Automated tests
# =============================================================================

async def test_mouse_scroll():
    """Test that scrolling works with ContentScroll."""
    app = TestScrollApp()
    async with app.run_test(size=(80, 20)) as pilot:
        scroll = app.query_one("#scroll", ContentScroll)

        # Should start at top
        assert scroll.scroll_y == 0, f"Expected scroll_y=0, got {scroll.scroll_y}"

        # Scroll down programmatically (simulates mouse wheel)
        scroll.scroll_down(animate=False)
        await pilot.pause()
        scroll.scroll_down(animate=False)
        await pilot.pause()
        assert scroll.scroll_y > 0, f"Scroll down didn't work: scroll_y={scroll.scroll_y}"
        saved_y = scroll.scroll_y

        # Scroll back up
        scroll.scroll_up(animate=False)
        await pilot.pause()
        assert scroll.scroll_y < saved_y, f"Scroll up didn't work: scroll_y={scroll.scroll_y}"

        print(f"  ✓ Scroll: down to {saved_y}, back up to {scroll.scroll_y}")


async def test_mouse_scroll_responsiveness():
    """Test that rapid scrolling remains responsive."""
    app = TestScrollApp()
    async with app.run_test(size=(80, 20)) as pilot:
        scroll = app.query_one("#scroll", ContentScroll)

        # Rapid scroll down
        for _ in range(10):
            scroll.scroll_down(animate=False)
        await pilot.pause()
        pos1 = scroll.scroll_y

        # Rapid scroll up
        for _ in range(10):
            scroll.scroll_up(animate=False)
        await pilot.pause()
        pos2 = scroll.scroll_y

        assert pos1 > 0, "Rapid scroll down failed"
        assert pos2 < pos1, "Rapid scroll up failed"
        print(f"  ✓ Rapid scroll: down to {pos1}, up to {pos2}")


async def test_tab_switching():
    """Test multi-agent tab switching."""
    Config.AGENT_NAME = "TestAgent"
    Config.AGENTS_HOME = tempfile.mkdtemp()

    # Create fake agent dirs
    for agent in ["TestAgent", "Agent2", "Agent3"]:
        os.makedirs(os.path.join(Config.AGENTS_HOME, agent, "asdaaas"), exist_ok=True)

    app = AsdaaasTUI(agents=["TestAgent", "Agent2", "Agent3"])
    async with app.run_test(size=(80, 30)) as pilot:
        # Should start on TestAgent
        assert app._active_agent == "TestAgent"

        # Switch to Agent2
        app.action_switch_agent("Agent2")
        await pilot.pause()
        assert app._active_agent == "Agent2"

        # Content scrolls should toggle visibility
        s1 = app.query_one("#content-TestAgent")
        s2 = app.query_one("#content-Agent2")
        assert s1.display == False
        assert s2.display == True

        # Switch back
        app.action_switch_agent("TestAgent")
        await pilot.pause()
        assert app._active_agent == "TestAgent"
        assert s1.display == True
        assert s2.display == False

        print("  ✓ Tab switching: visibility toggles correctly")


async def test_input_draft_persistence():
    """Test that input drafts save/restore on tab switch."""
    Config.AGENT_NAME = "TestAgent"
    Config.AGENTS_HOME = tempfile.mkdtemp()

    for agent in ["TestAgent", "Agent2"]:
        os.makedirs(os.path.join(Config.AGENTS_HOME, agent, "asdaaas"), exist_ok=True)

    app = AsdaaasTUI(agents=["TestAgent", "Agent2"])
    async with app.run_test(size=(80, 30)) as pilot:
        input_bar = app.query_one("#input-bar", MessageInput)

        # Type on TestAgent tab
        input_bar.insert("draft for test agent")
        await pilot.pause()

        # Switch to Agent2
        app.action_switch_agent("Agent2")
        await pilot.pause()
        assert input_bar.text == "", f"Input should be empty on Agent2, got: {input_bar.text}"

        # Type on Agent2 tab
        input_bar.insert("draft for agent 2")
        await pilot.pause()

        # Switch back to TestAgent
        app.action_switch_agent("TestAgent")
        await pilot.pause()
        assert input_bar.text == "draft for test agent", f"Draft not restored: {input_bar.text}"

        # Switch back to Agent2
        app.action_switch_agent("Agent2")
        await pilot.pause()
        assert input_bar.text == "draft for agent 2", f"Agent2 draft not restored: {input_bar.text}"

        print("  ✓ Input drafts: save and restore on tab switch")


async def test_slash_menu():
    """Test slash menu popup and navigation."""
    Config.AGENT_NAME = "TestAgent"
    Config.AGENTS_HOME = tempfile.mkdtemp()
    os.makedirs(os.path.join(Config.AGENTS_HOME, "TestAgent", "asdaaas"), exist_ok=True)

    app = AsdaaasTUI(agents=["TestAgent"])
    async with app.run_test(size=(80, 30)) as pilot:
        slash_menu = app.query_one("#slash-menu", SlashMenu)
        input_bar = app.query_one("#input-bar", MessageInput)

        # Menu should be hidden
        assert slash_menu.display == False

        # Type /
        input_bar.insert("/")
        await pilot.pause()
        assert slash_menu.display == True, "Slash menu should appear on /"

        # Type more to filter
        input_bar.insert("to")
        await pilot.pause()
        # Should show /todo options
        assert slash_menu.option_count > 0, "Should have matching options"

        # Clear
        input_bar.clear()
        await pilot.pause()
        assert slash_menu.display == False, "Menu should hide when input cleared"

        print(f"  ✓ Slash menu: shows on /, filters, hides on clear")


async def test_tool_panel_collapse():
    """Test tool panel collapse/expand."""
    Config.AGENT_NAME = "TestAgent"
    Config.AGENTS_HOME = tempfile.mkdtemp()
    os.makedirs(os.path.join(Config.AGENTS_HOME, "TestAgent", "asdaaas"), exist_ok=True)

    app = AsdaaasTUI(agents=["TestAgent"])
    async with app.run_test(size=(80, 30)) as pilot:
        content = app.query_one("#content-TestAgent", ContentScroll)

        # Create a completed tool panel
        panel = ToolCallPanel("test_id", "Test Tool", "read")
        content.mount(panel)
        panel.set_output("Some output text here")
        panel.set_status("completed")
        await pilot.pause()

        # Should be auto-collapsed
        assert panel._collapsed == True

        # Click to expand
        panel.on_click(None)
        await pilot.pause()
        assert panel._collapsed == False

        # Click to collapse again
        panel.on_click(None)
        await pilot.pause()
        assert panel._collapsed == True

        print("  ✓ Tool panel: auto-collapse, click expand/collapse")


async def test_gaze_selector_dismiss():
    """Test gaze selector dismisses on blur."""
    Config.AGENT_NAME = "TestAgent"
    Config.AGENTS_HOME = tempfile.mkdtemp()
    agent_dir = os.path.join(Config.AGENTS_HOME, "TestAgent", "asdaaas")
    os.makedirs(agent_dir, exist_ok=True)
    # Create minimal awareness.json
    with open(os.path.join(agent_dir, "awareness.json"), "w") as f:
        json.dump({"background_channels": {"#test": "doorbell"}}, f)
    with open(os.path.join(agent_dir, "gaze.json"), "w") as f:
        json.dump({"speech": {"target": "irc", "params": {"room": "pm:test"}}}, f)

    app = AsdaaasTUI(agents=["TestAgent"])
    async with app.run_test(size=(80, 30)) as pilot:
        selector = app.query_one("#gaze-selector", GazeSelector)

        # Open selector
        app.action_toggle_gaze_selector()
        await pilot.pause()
        assert selector.display == True

        # Simulate blur (focus moves away)
        selector.on_blur(None)
        await pilot.pause()
        assert selector.display == False

        print("  ✓ Gaze selector: dismiss on blur")


async def test_dynamic_footer():
    """Test footer switches between idle and generating states."""
    Config.AGENT_NAME = "TestAgent"
    Config.AGENTS_HOME = tempfile.mkdtemp()
    os.makedirs(os.path.join(Config.AGENTS_HOME, "TestAgent", "asdaaas"), exist_ok=True)

    app = AsdaaasTUI(agents=["TestAgent"])
    async with app.run_test(size=(80, 30)) as pilot:
        footer = app.query_one("#dynamic-footer", DynamicFooter)

        # Idle state
        footer.is_generating = False
        rendered = str(footer.render())
        assert "Clear" in rendered, f"Idle footer should show Clear: {rendered}"

        # Generating state
        footer.is_generating = True
        rendered = str(footer.render())
        assert "Interrupt" in rendered, f"Generating footer should show Interrupt: {rendered}"
        assert "Clear" not in rendered, f"Generating footer should not show Clear: {rendered}"

        print("  ✓ Dynamic footer: idle vs generating states")


# =============================================================================
# Runner
# =============================================================================

async def run_all_tests():
    tests = [
        ("Mouse scroll", test_mouse_scroll),
        ("Mouse scroll responsiveness", test_mouse_scroll_responsiveness),
        ("Tab switching", test_tab_switching),
        ("Input draft persistence", test_input_draft_persistence),
        ("Slash menu", test_slash_menu),
        ("Tool panel collapse", test_tool_panel_collapse),
        ("Gaze selector dismiss", test_gaze_selector_dismiss),
        ("Dynamic footer", test_dynamic_footer),
    ]

    print(f"\n{'='*60}")
    print(f"  asdaaas TUI Test Suite — {len(tests)} tests")
    print(f"{'='*60}\n")

    passed = 0
    failed = 0
    for name, test_fn in tests:
        try:
            await test_fn()
            passed += 1
        except Exception as e:
            print(f"  ✗ {name}: {e}")
            failed += 1

    print(f"\n{'='*60}")
    print(f"  Results: {passed} passed, {failed} failed")
    print(f"{'='*60}\n")
    return failed == 0


if __name__ == "__main__":
    if "--visual" in sys.argv:
        # Launch visual test app for manual mouse testing
        app = TestScrollApp()
        app.run()
    else:
        success = asyncio.run(run_all_tests())
        sys.exit(0 if success else 1)
