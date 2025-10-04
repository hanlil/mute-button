# Fix for bug in dynoselect, see gitbug issue https://github.com/woernerm/dynoselect/issues/5

import reflex_dynoselect

from typing import Dict, Literal, Optional, Callable
import reflex as rx
from reflex.components.radix.themes.typography.base import LiteralTextWeight
from reflex.components.radix.themes.base import LiteralRadius
from reflex.components.radix.themes.components.text_field import LiteralTextFieldSize
from reflex_dynoselect.utils import chevron_down
from reflex_dynoselect.options import Option
LiteralIndent = Literal[
    "0", "0.5", "1", "1.5", "2", "2.5", "3", "3.5", "4", "5", "6", "7", "8", "9", "10"
]


@classmethod
def patched_get_component(
        cls,
        default_option: Dict[str, str],
        placeholder: str,
        search_placeholder: str,
        size: LiteralTextFieldSize,
        weight: LiteralTextWeight,
        radius: LiteralRadius,
        height: str,
        padding: str,
        indent: LiteralIndent,
        align: str,
        create_option: Optional[Dict[str, str]] = None,
        on_select: Optional[Callable] = None,
        icon: str = None,
        content_props: dict[str, str] = {},
        root_props: dict[str, str] = {},
) -> rx.Component:
    """ Create the component. See dynoselect() function for more information.
    """
    size_radius = dict(size=size, radius=radius)
    opt_create = Option(**(create_option or {}))

    cls.get_fields()["selected"].default = default_option or cls._DEFAULT

    def hoverable(child, idx: int, **props) -> rx.Component:
        btn = rx.button(
            child, display="inline", variant="solid", size=size,
            style={
                ":not(:hover)": {"background": "transparent"},
                f":not(:hover) > {child.as_}": {"color": rx.color("gray", 12)}
            },
            **props
        )
        return rx.popover.close(btn)

    def entry(cond, option: Option, idx: int, create: bool = False) -> rx.Component:
        # Entries are either a text box or a solid button. This mapping ensures they
        # have the same height to avoid flicker effects when hovering over them.
        btn_height = {"1": "6", "2": "8", "3": "10", "4": "12"}
        indent_direction = {"center": "x", "left": "l", "right": "r"}
        select = opt_create.clone(label=cls.search_phrase) if create else option

        handler = lambda: cls.set_selected(select)
        if on_select:
            handler = lambda: [cls.set_selected(select), on_select(select)]

        button = hoverable(
            rx.text(
                option[cls._KEY_LABEL],
                trim="both",
                align=align,
                size=size,
                weight=weight,
                class_name=f"p{indent_direction[align]}-{indent} w-full"
            ),
            idx,
            radius=radius,
            align="center",  # Avoid flicker effects.
            padding="0",  # To align box and button texts for align="left".
            class_name=f"h-{btn_height[size]} w-full",
            on_click=handler
        )

        return rx.cond(cond, button, rx.fragment())

    #on_open_auto_focus = content_props.pop(
    #    "on_open_auto_focus", lambda *a: [cls.set_search_phrase("")]
    #)
    # This is the patch
    on_open_auto_focus = content_props.pop(
        "on_open_auto_focus", lambda: [cls.set_search_phrase("")]
    )

    return rx.popover.root(
        rx.popover.trigger(
            rx.button(
                # Show either placeholder or the selected option's label.
                rx.cond(
                    cls.selected[cls._KEY_LABEL] == "",
                    cls.btntext(
                        placeholder,
                        icon,
                        color=cls._COLOR_PLACEHOLDER,
                        weight=weight
                    ),
                    cls.btntext(
                        f"{cls.selected[cls._KEY_LABEL]}", icon, weight=weight
                    ),
                ),
                chevron_down(),
                class_name="rt-reset rt-SelectTrigger rt-variant-surface",
                **size_radius,
            ),
        ),
        rx.popover.content(
            rx.box(
                rx.input(
                    placeholder=(search_placeholder or ""),
                    on_change=cls.set_search_phrase,
                    **size_radius
                ),
                class_name=f"m-{padding}"
            ),
            rx.scroll_area(
                rx.flex(
                    rx.foreach(
                        cls.options,
                        lambda opt, i: entry(cls.client_search(opt), opt, i)
                    ),
                    (
                        entry(
                            ~cls.chained_options.lower().contains(cls.search_phrase.lower()),
                            opt_create.format(cls.search_phrase),
                            cls.options.length(),
                            True
                        ) if create_option else rx.fragment()
                    ),
                    direction="column",
                    class_name=f"w-full pr-{padding}",
                ),
                scrollbars="vertical",
                class_name=f"pl-{padding} mb-{padding}",
                radius=radius,
                height=height,
            ),
            overflow="hidden",
            padding="0",
            on_open_auto_focus=on_open_auto_focus,
            **content_props,
        ),
        **root_props,
    )

# Replace buggy function with patch
reflex_dynoselect.Dynoselect.get_component = patched_get_component
