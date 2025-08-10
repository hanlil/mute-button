import reflex as rx


def labeled_component(component: rx.Component, label: str):
    return rx.flex(
        rx.text(label, size='2'),
        component,
        direction='column',
        spacing='2',
    )

def titled_card(*children: rx.Component, title: str, title_spacing: str = '3'):
    return rx.card(
        rx.vstack(
            rx.box(
                rx.heading(title, size='4'),
                rx.divider(),
                width='100%',
            ),
            rx.box(
                *children
            ),
            spacing=title_spacing,
            width='100%',
        ),
        width = '100%',
    )
