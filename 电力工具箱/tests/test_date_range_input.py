"""Upstream range-picker behavior, using real Tk widgets and existing page variables."""
import gc
import tkinter as tk
from tkinter import ttk
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from toolbox import page_support
import toolbox_date_input as dates
from toolbox.runtime import TaskRegistry, ToolPaths

ROOT = Path(__file__).resolve().parents[2]


class DateRangeInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
            cls.root.geometry('1100x700+30+30')
            cls.root.update()
        except tk.TclError as exc:
            raise unittest.SkipTest(str(exc))

    @classmethod
    def tearDownClass(cls):
        gc.collect()
        cls.root.destroy()

    def tearDown(self):
        self.doCleanups()
        gc.collect()

    def make_range(self, start='2026-11-14', end='2026-11-21', optional=False):
        first = tk.StringVar(master=self.root, value=start)
        last = tk.StringVar(master=self.root, value=end)
        widget = dates.DateRangeInput(self.root, start_variable=first, end_variable=last,
                                      start_optional=optional)
        widget.pack()
        self.root.update()
        self.addCleanup(widget.destroy)
        return widget, first, last

    def test_same_month_two_real_button_commands_complete_and_close(self):
        widget, first, last = self.make_range()
        before = self.root.bind('<ButtonPress-1>')
        widget.open_calendar('start')
        def click_day(number):
            next(child for child in widget.left_days_frame.winfo_children()
                 if isinstance(child, ttk.Button) and child.cget('text') == str(number)).invoke()
        click_day(14)
        self.assertEqual(widget.stage, 'end')
        self.assertIsNotNone(widget.popup)
        click_day(21)
        self.assertEqual((first.get(), last.get()), ('2026-11-14', '2026-11-21'))
        self.assertIsNone(widget.popup)
        self.assertEqual(self.root.bind('<ButtonPress-1>'), before)
        self.assertIsNone(self.root.grab_current())

    def test_optional_start_stays_blank_when_only_end_is_selected(self):
        widget, first, last = self.make_range('', '2026-11-21', optional=True)
        widget.open_calendar('end')
        widget.pick(date(2026, 11, 22))
        self.assertEqual((first.get(), last.get()), ('', '2026-11-22'))
        self.assertIsNone(widget.popup)

    def test_switching_input_keeps_popup_and_chooses_the_requested_endpoint(self):
        widget, first, last = self.make_range()
        widget.open_calendar('start')
        popup = widget.popup
        widget.open_calendar('end')
        self.assertIs(widget.popup, popup)
        widget.pick(date(2026, 11, 25))
        self.assertEqual((first.get(), last.get()), ('2026-11-14', '2026-11-25'))
        self.assertIsNone(widget.popup)

    def test_reverse_and_cross_month_selections_never_invert_the_range(self):
        widget, first, last = self.make_range()
        widget.open_calendar('start')
        widget.pick(date(2026, 12, 2))
        widget.pick(date(2026, 11, 30))
        self.assertEqual((first.get(), last.get()), ('2026-11-30', '2026-11-30'))
        widget.open_calendar('start')
        widget.pick(date(2026, 11, 30))
        widget.pick(date(2026, 12, 2))
        self.assertEqual((first.get(), last.get()), ('2026-11-30', '2026-12-02'))

    def test_independent_navigation_and_relinking(self):
        widget, _, _ = self.make_range()
        widget.open_calendar()
        widget.change_month('left', 1)
        self.assertEqual((widget.left_month, widget.right_month), (date(2026, 12, 1), date(2027, 1, 1)))
        widget.set_linked(False)
        previous_right = widget.right_month
        widget.change_month('left', -3)
        self.assertEqual(widget.right_month, previous_right)
        widget.set_linked(True)
        self.assertEqual(widget.right_month, dates.shift_month(widget.left_month, 1))

    def test_confirm_escape_and_outside_click_release_bindings(self):
        widget, _, _ = self.make_range()
        before = self.root.bind('<ButtonPress-1>')
        for close in (widget.confirm,
                      lambda: widget._outside_click(SimpleNamespace(x_root=-1, y_root=-1))):
            widget.open_calendar()
            close()
            self.assertIsNone(widget.popup)
            self.assertEqual(self.root.bind('<ButtonPress-1>'), before)
        self.assertEqual(dates.shift_month(date(1, 1, 1), -12), date(1, 1, 1))
        self.assertEqual(dates.shift_month(date(9999, 12, 1), 12), date(9999, 12, 1))

    def test_existing_range_pages_share_variables_and_optional_start_contract(self):
        from toolbox.pages import SpotPriceUpdatePage, TransactionInfoUpdatePage
        def find(widget):
            for child in widget.winfo_children():
                if isinstance(child, dates.DateRangeInput):
                    return child
                result = find(child)
                if result is not None:
                    return result
        for page_type, optional in ((SpotPriceUpdatePage, False), (TransactionInfoUpdatePage, True)):
            with self.subTest(page=page_type.__name__):
                page = page_type(self.root, ToolPaths(ROOT), TaskRegistry())
                try:
                    picker = find(page)
                    self.assertIsNotNone(picker)
                    self.assertIs(picker.start_variable, page.start_date)
                    self.assertIs(picker.end_variable, page.end_date)
                    self.assertEqual(picker.start_optional, optional)
                finally:
                    page.destroy()

    def test_adjacent_month_placeholder_can_finish_selection(self):
        widget, first, last = self.make_range()
        widget.open_calendar('start')
        widget.pick(date(2026, 11, 30))
        self.root.update()
        cell = next(child for child in widget.left_days_frame.winfo_children()
                    if isinstance(child, ttk.Label) and child.cget('style') == 'CalendarRangeAdjacent.TLabel'
                    and child.cget('text') == '1')
        cell.event_generate('<Button-1>', x=5, y=5)
        self.assertEqual((first.get(), last.get()), ('2026-11-30', '2026-12-01'))
        self.assertIsNone(widget.popup)

    def test_keyboard_escape_closes_the_windows_popup(self):
        widget, _, _ = self.make_range()
        widget._open_from_key('start')
        self.root.update()
        widget.popup.event_generate('<Escape>')
        self.root.update()
        self.assertIsNone(widget.popup)

    def test_return_normalizes_dates_and_closes_popup(self):
        widget, first, last = self.make_range('2026-11-4', '2026/11/5')
        widget.open_calendar('start')
        widget.end_entry.focus_force()
        self.root.update()
        widget.end_entry.event_generate('<Return>')
        self.root.update()
        self.assertEqual((first.get(), last.get()), ('2026-11-04', '2026-11-05'))
        self.assertIsNone(widget.popup)
