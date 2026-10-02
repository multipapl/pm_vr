"""Secondary UI sections; expansion lives in Blender's region, not scene data."""


def section(layout, identifier, title, icon='NONE', default_closed=True):
    header, body = layout.panel(identifier, default_closed=default_closed)
    header.label(text=title, icon=icon)
    return body
