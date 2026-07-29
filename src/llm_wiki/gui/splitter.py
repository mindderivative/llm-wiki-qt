"""Drag-to-resize handles for the dock layout.

Flet ships no split-pane control, but one is only a `GestureDetector` over
a divider that resizes its neighbour -- the technique `flet-contrib`'s
`VerticalSplitter` demonstrates. That package targets the pre-0.80 API
(it reads `e.delta_x`, which 0.86 replaced with `e.local_delta`) and is
unmaintained, so the handle is written here directly instead.

Unlike a two-pane splitter, a handle resizes one adjacent fixed-size
container, which composes into the three-column dock layout without
nesting.
"""

import contextlib

import flet as ft

_THICKNESS = 5


class ResizeHandle(ft.GestureDetector):
    """A draggable divider that resizes `target`'s width or height.

    `sign` is +1 when growing `target` means dragging away from the origin
    (a handle to the right of a left-hand pane) and -1 when it means
    dragging toward it (a handle to the left of a right-hand pane).
    """

    def __init__(
        self,
        target: ft.Container,
        *,
        horizontal: bool = True,
        sign: int = 1,
        min_size: float = 120,
        max_size: float = 600,
    ) -> None:
        self.target = target
        self.horizontal = horizontal
        self.sign = sign
        self.min_size = min_size
        self.max_size = max_size

        super().__init__(
            content=(
                ft.VerticalDivider(width=_THICKNESS, thickness=1)
                if horizontal
                else ft.Divider(height=_THICKNESS, thickness=1)
            ),
            drag_interval=10,
            # COLUMN/ROW (GTK `col-resize`/`row-resize`) rather than
            # LEFT_RIGHT/UP_DOWN: these are the pane-divider cursors, and the
            # arrow variants are the ones GTK logs "Unable to load  from the
            # cursor theme" for when a theme has no matching entry.
            mouse_cursor=(
                ft.MouseCursor.RESIZE_COLUMN if horizontal else ft.MouseCursor.RESIZE_ROW
            ),
            on_pan_update=self._on_drag,
        )

    def _on_drag(self, e: ft.DragUpdateEvent) -> None:
        delta = e.local_delta.x if self.horizontal else e.local_delta.y
        current = self.target.width if self.horizontal else self.target.height
        new_size = max(self.min_size, min(self.max_size, current + delta * self.sign))

        if self.horizontal:
            self.target.width = new_size
        else:
            self.target.height = new_size

        # Suppressed while unattached: the initial build, and headless tests.
        with contextlib.suppress(RuntimeError):
            self.target.update()
